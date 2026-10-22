import type { AmbientSaveState } from '../../../../core/src/ambientSave.js';
import type { CityPresentation } from '../../../../core/src/cityPresentation.js';
import type { DiOfficeState } from '../../../../core/src/messages.js';
import { pickDiversePalette } from '../../../../core/src/paletteUtils.js';
import {
  AMBIENT_CATCHUP_MAX_SEC,
  AMBIENT_CATCHUP_STEP_SEC,
  AUTO_ON_FACING_DEPTH,
  AUTO_ON_SIDE_DEPTH,
  CHARACTER_HIT_HALF_WIDTH,
  CHARACTER_HIT_HEIGHT,
  CHARACTER_SITTING_OFFSET_PX,
  CITY_LIGHT_AGENT_LIFT_PX,
  CITY_LIGHT_VISIBLE_EPSILON,
  DI_RECEPTIONIST_ID,
  DI_RECEPTIONIST_PALETTE,
  DISMISS_BUBBLE_FAST_FADE_SEC,
  FURNITURE_ANIM_INTERVAL_SEC,
  GREETER_ID,
  GREETER_TILE_MARGIN,
  INACTIVE_SEAT_TIMER_MIN_SEC,
  INACTIVE_SEAT_TIMER_RANGE_SEC,
  MAX_PET_ID_LENGTH,
  PET_HIT_HALF_WIDTH,
  PET_HIT_HEIGHT,
  TRAFFIC_CAP_LOW,
  TRAFFIC_MAX_VEHICLES,
  TRAFFIC_PEDESTRIAN_LOOKAHEAD_TILES,
  WAITING_BUBBLE_DURATION_SEC,
} from '../../constants.js';
import type { AmbientRestoreResult } from '../ambient/ambientSave.js';
import { applyAmbientSnapshot, serializeAmbient } from '../ambient/ambientSave.js';
import type { AmbientResident, AmbientWorld } from '../ambient/ambientWorld.js';
import { buildAmbientWorld } from '../ambient/ambientWorld.js';
import { CityEffects } from '../city/cityEffects.js';
import { cityMaskFor, lampLights } from '../city/cityMask.js';
import type { CityRenderInput } from '../city/cityRender.js';
import { weatherVeil } from '../city/cityWeather.js';
import { getColorizedSprite } from '../colorize.js';
import { haloEligibleCharacterIds } from '../halo.js';
import type { DiOfficeDetection } from '../layout/diOffices.js';
import { detectOffices } from '../layout/diOffices.js';
import { getAnimationFrames, getCatalogEntry, getOnStateType } from '../layout/furnitureCatalog.js';
import {
  createDefaultLayout,
  getBlockedTiles,
  getDeskTiles,
  layoutToFurnitureInstances,
  layoutToSeats,
  layoutToTileMap,
} from '../layout/layoutSerializer.js';
import type { ReceptionPost } from '../layout/receptionPost.js';
import {
  findReceptionPost,
  isReceptionHit,
  keepsNeighboursConnected,
  receptionReservedSeatIds,
} from '../layout/receptionPost.js';
import { electronicsTileSet, seatFacesElectronics } from '../layout/seatElectronics.js';
import { findPath, getWalkableTiles, isWalkable } from '../layout/tileMap.js';
import type { FootprintOf } from '../layout/worldViews.js';
import { inRegions, livingRegions, livingTiles as confineLiving } from '../layout/worldViews.js';
import { boundRenderCaches } from '../renderCaches.js';
import { getPetCount, getPetName } from '../sprites/petSpriteData.js';
import { getLoadedCharacterCount } from '../sprites/spriteData.js';
import { buildTrafficWorld } from '../traffic/trafficBuild.js';
import { vehicleAt } from '../traffic/trafficRender.js';
import type { TrafficPeople, TrafficWorld, Vehicle } from '../traffic/trafficWorld.js';
import type { Train } from '../transit/transitWorld.js';
import { deriveTransitLine, TransitWorld } from '../transit/transitWorld.js';
import type {
  Character,
  FurnitureInstance,
  OfficeLayout,
  Pet,
  PlacedFurniture,
  PlacedPet,
  Seat,
  SpriteData,
  TileType as TileTypeVal,
} from '../types.js';
import {
  CharacterState,
  Direction,
  MATRIX_EFFECT_DURATION,
  PetState,
  TILE_SIZE,
} from '../types.js';
import { createCharacter, settleCharacter, updateCharacter } from './characters.js';
import { advanceMatrixEffect, startMatrixEffect } from './matrixEffectState.js';
import { createPet, updatePet } from './petEntity.js';
import { anchorTile, closestFreeSeat } from './seatPlacement.js';

const footprintOf: FootprintOf = (type) => {
  const entry = getCatalogEntry(type);
  return entry
    ? { w: entry.footprintW, h: entry.footprintH, seat: entry.category === 'chairs' }
    : null;
};

/**
 * The walkable tiles characters and pets LIVE on: wander targets and spawn points. In a large
 * world only the office and its deck (worldViews.livingRegions), and of those only what a seat
 * can reach — the woodland is scenery, and a uniform pick over all its walkable tiles would walk
 * the live team out of view. A legacy-size layout keeps every walkable tile, exactly as before.
 */
function livingTiles(
  layout: OfficeLayout,
  tileMap: TileTypeVal[][],
  blockedTiles: Set<string>,
  seats: Map<string, Seat>,
  detection: DiOfficeDetection | null = null,
): Array<{ col: number; row: number }> {
  const regions = livingRegions(layout, footprintOf);
  // the detected satellite offices are places agents live and wander too (P2-C); the home and deck stay as they were
  const living =
    regions && detection
      ? [...regions, ...detection.offices.filter((o) => o.kind === 'district').map((o) => o.box)]
      : regions;
  return confineLiving(
    getWalkableTiles(tileMap, blockedTiles),
    living,
    [...seats.values()].map((s) => ({ col: s.seatCol, row: s.seatRow })),
  );
}

/**
 * The SEATS of a layout. In a LARGE world only the chairs inside the home office and its deck
 * (worldViews.livingRegions) are seats: a chair furnishing a satellite office is scenery — drawn and
 * colliding like any furniture — so no bound worker is ever assigned, restored or re-seated outside
 * the home. Legacy-size layouts (or a large one with no home seat at all) keep every chair.
 */
function homeSeats(layout: OfficeLayout, seats: Map<string, Seat>): Map<string, Seat> {
  const regions = livingRegions(layout, footprintOf);
  if (!regions) return seats;
  const kept = new Map([...seats].filter(([, s]) => inRegions(s.seatCol, s.seatRow, regions)));
  return kept.size > 0 ? kept : seats;
}

export class OfficeState {
  layout: OfficeLayout;
  tileMap: TileTypeVal[][];
  seats: Map<string, Seat>;
  blockedTiles: Set<string>;
  furniture: FurnitureInstance[];
  walkableTiles: Array<{ col: number; row: number }>;
  characters: Map<number, Character> = new Map();
  pets: Pet[] = [];
  /**
   * AMBIENT RESIDENTS (DI mode only; decorative). Deliberately NOT in `characters`, for the same
   * reason as the greeter: every agent loop stays correct by default. Null while disabled.
   */
  ambient: AmbientWorld | null = null;
  /** The decorative subway (DI mode, worlds with a line); built and dropped with the residents. */
  transit: TransitWorld | null = null;
  /** The PRESENTED DI office state (DI mode only): the server projection after the client's own
   *  invalidation (useExtensionMessages). The working halo reads this and nothing else. */
  diOffice: DiOfficeState | null = null;
  /** Seconds the decorative simulation has actually advanced (frames and catch-up alike). */
  ambientClock = 0;
  /** A saved ambient snapshot from the handshake, applied once to the next ambient build. */
  private pendingAmbientSnapshot: unknown = null;
  /** A restore attempt has run against the held handshake snapshot (its outcome is in `lastAmbientRestore`). */
  private ambientAttemptRecorded = false;
  /** Set when the held snapshot's migration was refused as unsupported: nothing is saved over that file this session. */
  private ambientSaveWithheld: string | null = null;
  /** What the last snapshot application did (evidence; null if none was attempted). */
  lastAmbientRestore: AmbientRestoreResult | null = null;
  private ambientEnabled = false;
  /** Resident under the pointer (hover label). Never an agent id. */
  hoveredAmbientId: string | null = null;
  /** Accumulated time for furniture animation frame cycling */
  furnitureAnimTimer = 0;
  /** Bumped whenever the furniture instances change for a reason OTHER than an animation frame
   *  (layout rebuild, auto-state ON/OFF swap): what a cache of the STATIC scene keys on. */
  furnitureStaticVersion = 0;
  /** Sprites for furniture whose auto-on state animates, indexed into `furniture`. Null until the
   *  first animation tick has indexed them. After that, a frame change swaps those sprites and
   *  does not rebuild the whole metropolis (that rebuild was the slow cluster in the frame p95). */
  private furnitureAnimSlots: { index: number; sprites: SpriteData[] }[] | null = null;
  /** REDUCED MOTION — set only in DI mode, from prefers-reduced-motion. Transitions settle
   *  at once and nothing moves on its own; it never changes activity, live or any other data.
   *  Normal Pixel never sets it, so its behaviour is unchanged. */
  reducedMotion = false;

  /** The decorative city's lighting and weather (living city 6-9): the viewer's choices and what is shown.
   *  Local decoration; it reads no Mission or agent state and writes none. */
  readonly city = new CityEffects();

  /** The decorative traffic (living city 6-9): the world's own lane-aligned vehicle furniture, brought to life.
   *  Built lazily, only in the DI office and only while the choice is not 'off'; rebuilt when the layout is
   *  replaced. It reads where people are (to give way) and writes nothing outside itself. */
  private traffic: TrafficWorld | null = null;
  private readonly trafficPeople: { agentOwn: number[]; agentNear: number[]; walkerOwn: number[]; walkerNear: number[] } = {
    agentOwn: [],
    agentNear: [],
    walkerOwn: [],
    walkerNear: [],
  };
  /** Vehicle under the pointer (hover label). Never an agent id. */
  hoveredVehicleId: string | null = null;
  selectedAgentId: number | null = null;
  cameraFollowId: number | null = null;
  hoveredAgentId: number | null = null;
  hoveredTile: { col: number; row: number } | null = null;
  /** Maps "parentId:toolId" → sub-agent character ID (negative) */
  subagentIdMap: Map<string, number> = new Map();
  /** Reverse lookup: sub-agent character ID → parent info */
  subagentMeta: Map<number, { parentAgentId: number; parentToolId: string }> = new Map();
  private nextSubagentId = -1;

  /**
   * folderName → list of Area labels that workspace folder belongs to.
   * Populated by useExtensionMessages on `areaMappingsLoaded`. Consulted by
   * `findFreeSeat()` to bias new agents toward seats inside their folder's Area.
   */
  areaMappings: Record<string, string[]> = {};

  /**
   * The first-run consent greeter, deliberately NOT in `characters`.
   *
   * `characters` means "agents": everything that iterates it — seat
   * assignment, palette diversity, the wander FSM, hit-testing, the seat
   * payload the webview persists — is asking an agent question the greeter has
   * no answer to. Holding it here instead of tagging it with a flag makes
   * every one of those loops correct by default, rather than correct as long
   * as each remembers an `isGreeter` guard. It is drawn because
   * `getCharacters()` appends it, and that is the only place it joins the
   * others.
   */
  greeter: Character | null = null;

  /** World-space point the camera drifts to while the greeter is up
   *  (the bubble overlay recomputes it every frame: the combined center of the
   *  character and its speech bubble). An explicit cameraFollowId outranks it. */
  greeterCameraTarget: { x: number; y: number } | null = null;
  /** Latched by a manual pan during the ask: the user took the camera, so the
   *  overlay's per-frame updates stop re-centering. Reset on spawn/despawn. */
  private greeterCameraCancelled = false;

  /**
   * The DI reception desk's decorative receptionist, deliberately NOT in `characters` (the greeter's
   * reasoning, applied again): every agent loop — seat assignment, selection, the working glow, seat
   * persistence, counts, the wander FSM — is an agent question it has no answer to, so holding it
   * here makes each of them correct by default. It joins the others only in `getCharacters()` (drawn)
   * and as a tile residents give way to. Present whenever the loaded layout has a reception desk
   * (zero real agents included), rebuilt with the layout, never moved, never selectable.
   */
  receptionist: Character | null = null;
  /** The reception desk and the receptionist's tile, or null (no desk in this layout, or not DI). */
  receptionPost: ReceptionPost | null = null;
  private receptionistEnabled = false;
  /** P2-C evidence (read-only): the detected working offices with their seats and workstations, or null (legacy seating). */
  officeDetection: DiOfficeDetection | null = null;
  /** The receptionist's reserved workstation seat ids: never a seat a real agent can hold (empty without a post). */
  reservedSeatIds: ReadonlySet<string> = new Set();
  /** `col,row` of the stand tile while it is blocked for agents, pets and the walk mask the real characters use (else null). */
  private standTileKey: string | null = null;
  /** The PREVIOUS build's seat list (every home seat, the reception bench included): the residents' predecessor derivation and sizing use it. */
  private legacySeatList: Seat[] = [];

  setAreaMappings(mappings: Record<string, string[]>): void {
    this.areaMappings = mappings;
  }

  constructor(layout?: OfficeLayout) {
    this.layout = layout || createDefaultLayout();
    this.tileMap = layoutToTileMap(this.layout);
    this.seats = new Map();
    this.blockedTiles = new Set();
    this.deriveSeating(this.layout);
    this.furniture = layoutToFurnitureInstances(this.layout.furniture);
    this.walkableTiles = livingTiles(
      this.layout,
      this.tileMap,
      this.blockedTiles,
      this.seats,
      this.officeDetection,
    );
    // Pets are built last because they need walkableTiles populated for spawn.
    this.rebuildPetsFromLayout(this.layout);
  }

  /**
   * Derive everything that decides WHERE real characters may stand and sit, from the layout (and, in the DI office, the reception post):
   *   1. the reception post (DI office only) and the receptionist's reserved workstation — never a seat a real agent can hold;
   *   2. the stand tile, blocked for agents, pets and spawning only when blocking it cuts nothing in two (`keepsNeighboursConnected`, once per layout);
   *   3. the three-office detection (DI office only; explicit-home large worlds with office districts) → `seats` = the workstations/chairs of the
   *      detected offices; no detection → `homeSeats` exactly as before. The reserved seats are withheld in both cases.
   * A layout with no post, and every layout outside the DI office, keeps its seats and blocked tiles exactly.
   */
  private deriveSeating(layout: OfficeLayout): void {
    const blocked = getBlockedTiles(layout.furniture);
    const allSeats = layoutToSeats(layout.furniture);
    const walk = (c: number, r: number): boolean => isWalkable(c, r, this.tileMap, blocked);
    const post = this.receptionistEnabled
      ? findReceptionPost(layout, footprintOf, walk, (c, r) =>
          keepsNeighboursConnected(c, r, walk, layout.cols, layout.rows),
        )
      : null;
    const reserved = receptionReservedSeatIds(post, allSeats);
    this.legacySeatList = [...homeSeats(layout, allSeats).values()];
    this.receptionPost = post;
    this.reservedSeatIds = reserved;
    this.standTileKey = null;
    if (post?.tileBlocked) {
      this.standTileKey = `${post.standCol},${post.standRow}`;
      blocked.add(this.standTileKey);
    }
    this.blockedTiles = blocked;
    const detection = this.receptionistEnabled
      ? detectOffices({
          layout,
          seats: allSeats,
          footprintOf,
          deskTiles: getDeskTiles(layout.furniture),
          electronicsTiles: electronicsTileSet(layout.furniture),
          isWalkable: walk,
          reservedSeatIds: reserved,
        })
      : null;
    this.officeDetection = detection;
    if (detection) {
      this.seats = new Map([...allSeats].filter(([uid]) => detection.officeIndexBySeat.has(uid)));
    } else {
      const home = homeSeats(layout, allSeats);
      this.seats =
        reserved.size === 0 ? home : new Map([...home].filter(([uid]) => !reserved.has(uid)));
    }
  }

  /**
   * Reassign every character to the CURRENT seats, keeping each one's existing seat whenever that seat is still a seat and free; a
   * character whose seat is gone (removed, reserved for the receptionist, outside the detected offices) takes a free one, else stands.
   * The first half of what `rebuildFromLayout` always did, shared with the switches that change which seats exist.
   */
  private reseatCharacters(): void {
    for (const seat of this.seats.values()) {
      seat.assigned = false;
    }

    // First pass: try to keep characters at their existing seats
    for (const ch of this.characters.values()) {
      if (ch.seatId && this.seats.has(ch.seatId)) {
        const seat = this.seats.get(ch.seatId)!;
        if (!seat.assigned) {
          seat.assigned = true;
          // Snap character to seat position
          ch.tileCol = seat.seatCol;
          ch.tileRow = seat.seatRow;
          const cx = seat.seatCol * TILE_SIZE + TILE_SIZE / 2;
          const cy = seat.seatRow * TILE_SIZE + TILE_SIZE / 2;
          ch.x = cx;
          ch.y = cy;
          ch.dir = seat.facingDir;
          continue;
        }
      }
      ch.seatId = null; // will be reassigned below
    }

    // Second pass: assign remaining characters to free seats
    for (const ch of this.characters.values()) {
      if (ch.seatId) continue;
      const seatId = this.findFreeSeat(ch.folderName);
      if (seatId) {
        this.seats.get(seatId)!.assigned = true;
        ch.seatId = seatId;
        const seat = this.seats.get(seatId)!;
        ch.tileCol = seat.seatCol;
        ch.tileRow = seat.seatRow;
        ch.x = seat.seatCol * TILE_SIZE + TILE_SIZE / 2;
        ch.y = seat.seatRow * TILE_SIZE + TILE_SIZE / 2;
        ch.dir = seat.facingDir;
      }
    }
  }

  /** Rebuild all derived state from a new layout. Reassigns existing characters.
   *  @param shift Optional pixel shift to apply when grid expands left/up */
  rebuildFromLayout(layout: OfficeLayout, shift?: { col: number; row: number }): void {
    // the render-cache bound across edits, at this safe point BEFORE the instances are rebuilt
    boundRenderCaches();
    this.layout = layout;
    this.tileMap = layoutToTileMap(layout);
    this.deriveSeating(layout);
    this.traffic = null; // its lanes came from the old layout
    this.rebuildFurnitureInstances();
    this.walkableTiles = livingTiles(
      layout,
      this.tileMap,
      this.blockedTiles,
      this.seats,
      this.officeDetection,
    );
    this.syncTraffic();

    // Shift character positions when grid expands left/up
    if (shift && (shift.col !== 0 || shift.row !== 0)) {
      for (const ch of this.characters.values()) {
        ch.tileCol += shift.col;
        ch.tileRow += shift.row;
        ch.x += shift.col * TILE_SIZE;
        ch.y += shift.row * TILE_SIZE;
        // Clear path since tile coords changed
        ch.path = [];
        ch.moveProgress = 0;
      }
    }

    // Shift pet positions when grid expands left/up
    if (shift && (shift.col !== 0 || shift.row !== 0)) {
      for (const pet of this.pets) {
        pet.tileCol += shift.col;
        pet.tileRow += shift.row;
        pet.x += shift.col * TILE_SIZE;
        pet.y += shift.row * TILE_SIZE;
        pet.path = [];
        pet.moveProgress = 0;
      }
    }

    this.reseatCharacters();

    // Relocate any characters that ended up outside bounds or on non-walkable tiles
    for (const ch of this.characters.values()) {
      if (ch.seatId) continue; // seated characters are fine
      if (
        ch.tileCol < 0 ||
        ch.tileCol >= layout.cols ||
        ch.tileRow < 0 ||
        ch.tileRow >= layout.rows
      ) {
        this.relocateCharacterToWalkable(ch);
      }
    }

    // Relocate any pets that ended up outside bounds or on non-walkable tiles
    for (const pet of this.pets) {
      if (
        pet.tileCol < 0 ||
        pet.tileCol >= layout.cols ||
        pet.tileRow < 0 ||
        pet.tileRow >= layout.rows ||
        !isWalkable(pet.tileCol, pet.tileRow, this.tileMap, this.blockedTiles)
      ) {
        if (this.walkableTiles.length > 0) {
          const spawn = this.walkableTiles[Math.floor(Math.random() * this.walkableTiles.length)];
          pet.tileCol = spawn.col;
          pet.tileRow = spawn.row;
          pet.x = spawn.col * TILE_SIZE + TILE_SIZE / 2;
          pet.y = spawn.row * TILE_SIZE + TILE_SIZE / 2;
          pet.path = [];
          pet.moveProgress = 0;
          pet.state = PetState.IDLE;
          pet.frame = 0;
          pet.frameTimer = 0;
          pet.followTargetId = null;
        }
      }
    }

    // Reconcile pets against the layout roster (handles editor add/remove)
    this.rebuildPetsFromLayout(layout);
    // Ambient residents are derived from the world: rebuilt (deterministically) with it.
    this.rebuildAmbient();
    this.rebuildReceptionist();
  }

  /** Move a character to a random walkable tile */
  private relocateCharacterToWalkable(ch: Character): void {
    if (this.walkableTiles.length === 0) return;
    const spawn = this.walkableTiles[Math.floor(Math.random() * this.walkableTiles.length)];
    ch.tileCol = spawn.col;
    ch.tileRow = spawn.row;
    ch.x = spawn.col * TILE_SIZE + TILE_SIZE / 2;
    ch.y = spawn.row * TILE_SIZE + TILE_SIZE / 2;
    ch.path = [];
    ch.moveProgress = 0;
  }

  getLayout(): OfficeLayout {
    return this.layout;
  }

  /** Get the blocked-tile key for a character's own seat, or null */
  private ownSeatKey(ch: Character): string | null {
    if (!ch.seatId) return null;
    const seat = this.seats.get(ch.seatId);
    if (!seat) return null;
    return `${seat.seatCol},${seat.seatRow}`;
  }

  /** Temporarily unblock a character's own seat, run fn, then re-block */
  private withOwnSeatUnblocked<T>(ch: Character, fn: () => T): T {
    const key = this.ownSeatKey(ch);
    if (key) this.blockedTiles.delete(key);
    const result = fn();
    if (key) this.blockedTiles.add(key);
    return result;
  }

  /** Collect every tile occupied by electronics furniture (PCs, monitors, etc.). */
  private buildElectronicsTileSet(): Set<string> {
    return electronicsTileSet(this.layout.furniture);
  }

  /** Find the area label assigned to a seat's tile, or null. Public for e2e
   *  observability (getAgentSeats hook reads a seated agent's area). */
  seatZone(uid: string): string | null {
    const seat = this.seats.get(uid);
    if (!seat) return null;
    const tiles = this.layout.areaTiles;
    if (!tiles || tiles.length === 0) return null;
    const idx = seat.seatRow * this.layout.cols + seat.seatCol;
    if (idx < 0 || idx >= tiles.length) return null;
    return tiles[idx] ?? null;
  }

  /** Does this seat face an electronics tile (PC, monitor)? The shared auto-on rule (office/layout/seatElectronics.ts). */
  private isSeatFacingElectronics(seat: Seat, electronicsTiles: ReadonlySet<string>): boolean {
    return seatFacesElectronics(seat, electronicsTiles);
  }

  /**
   * Random-pick a seat from a candidate list, biased toward seats that face an
   * electronics tile. Returns null when the candidate list is empty.
   */
  private pickFromSeats(seatUids: string[], electronicsTiles: Set<string>): string | null {
    if (seatUids.length === 0) return null;
    const pcSeats: string[] = [];
    const otherSeats: string[] = [];
    for (const uid of seatUids) {
      const seat = this.seats.get(uid);
      if (!seat) continue;
      if (this.isSeatFacingElectronics(seat, electronicsTiles)) {
        pcSeats.push(uid);
      } else {
        otherSeats.push(uid);
      }
    }
    if (pcSeats.length > 0) return pcSeats[Math.floor(Math.random() * pcSeats.length)];
    if (otherSeats.length > 0) return otherSeats[Math.floor(Math.random() * otherSeats.length)];
    return null;
  }

  /**
   * 3-stage seat picker for top-level agents.
   *
   *   Stage 1: If `folderName` is given and `areaMappings[folderName]` lists
   *            Area labels, prefer free seats whose tile is labeled with one
   *            of those areas.
   *   Stage 2: Prefer free seats whose tile has NO area label (unzoned).
   *   Stage 3: Any free seat.
   *
   * Each stage routes through `pickFromSeats` for the PC-bias rule. Returns
   * null only when every seat is already occupied. Passing `undefined`
   * preserves pre-Areas single-stage behavior (skips Stage 1; Stage 2 picks
   * unzoned seats from a layout without `areaTiles`, which is every seat).
   */
  private findFreeSeat(folderName?: string): string | null {
    const electronicsTiles = this.buildElectronicsTileSet();
    const freeSeats: string[] = [];
    for (const [uid, seat] of this.seats) {
      if (!seat.assigned) freeSeats.push(uid);
    }
    if (freeSeats.length === 0) return null;

    const areaLabels = folderName ? this.areaMappings[folderName] : undefined;

    // Stage 1 — in-area seats for the folder's mapped Area labels.
    if (areaLabels && areaLabels.length > 0) {
      const wanted = new Set(areaLabels);
      const inArea = freeSeats.filter((uid) => {
        const label = this.seatZone(uid);
        return label !== null && wanted.has(label);
      });
      const pick = this.pickFromSeats(inArea, electronicsTiles);
      if (pick) return pick;
    }

    // Stage 2 — unzoned seats (no area label, or layout has no areas at all).
    const unzoned = freeSeats.filter((uid) => this.seatZone(uid) === null);
    const pick2 = this.pickFromSeats(unzoned, electronicsTiles);
    if (pick2) return pick2;

    // Stage 3 — any free seat.
    return this.pickFromSeats(freeSeats, electronicsTiles);
  }

  /** Closest walkable tile to (col,row) not occupied by another character, or null. */
  private closestFreeWalkableTile(col: number, row: number): { col: number; row: number } | null {
    const occupied = new Set<string>();
    for (const ch of this.characters.values()) {
      occupied.add(`${ch.tileCol},${ch.tileRow}`);
    }
    let best: { col: number; row: number } | null = null;
    let bestDist = Infinity;
    for (const tile of this.walkableTiles) {
      if (occupied.has(`${tile.col},${tile.row}`)) continue;
      const d = Math.abs(tile.col - col) + Math.abs(tile.row - row);
      if (d < bestDist) {
        best = tile;
        bestDist = d;
      }
    }
    return best;
  }

  /**
   * Pick a diverse palette for a new agent based on currently active agents.
   * First 6 agents each get a unique skin (random order). Beyond 6, skins
   * repeat in balanced rounds with a random hue shift (≥45°).
   */
  private pickDiversePalette(): { palette: number; hueShift: number } {
    // Count how many non-sub-agents use each base palette (0-5)
    const paletteCount = getLoadedCharacterCount();
    const counts = new Array(paletteCount).fill(0) as number[];
    for (const ch of this.characters.values()) {
      if (ch.isSubagent) continue;
      if (ch.palette < paletteCount) counts[ch.palette]++;
    }
    return pickDiversePalette(paletteCount, counts);
  }

  addAgent(
    id: number,
    preferredPalette?: number,
    preferredHueShift?: number,
    preferredSeatId?: string,
    skipSpawnEffect?: boolean,
    folderName?: string,
    nearAgentId?: number,
  ): void {
    if (this.characters.has(id)) return;

    let palette: number;
    let hueShift: number;
    if (preferredPalette !== undefined) {
      palette = preferredPalette;
      hueShift = preferredHueShift ?? 0;
    } else {
      const pick = this.pickDiversePalette();
      palette = pick.palette;
      hueShift = pick.hueShift;
    }

    // Try preferred seat first, then (for teammates) the seat closest to the
    // anchor agent, then any free seat. anchorTile resolves to the anchor's SEAT
    // (stable from creation) rather than its live tile, so a teammate placed while
    // the lead is still walking to its seat still clusters around the final seat.
    const anchor = nearAgentId !== undefined ? this.characters.get(nearAgentId) : undefined;
    const anchorAt = anchorTile(anchor, this.seats);
    let seatId: string | null = null;
    if (preferredSeatId && this.seats.has(preferredSeatId)) {
      const seat = this.seats.get(preferredSeatId)!;
      if (!seat.assigned) {
        seatId = preferredSeatId;
      }
    }
    if (!seatId && anchorAt) {
      seatId = closestFreeSeat(this.seats, anchorAt.col, anchorAt.row);
    }
    if (!seatId) {
      seatId = this.findFreeSeat(folderName);
    }

    let ch: Character;
    if (seatId) {
      const seat = this.seats.get(seatId)!;
      seat.assigned = true;
      ch = createCharacter(id, palette, seatId, seat, hueShift);
    } else {
      // No seats — teammates spawn beside their anchor, others at a random walkable tile
      let spawn = anchorAt ? this.closestFreeWalkableTile(anchorAt.col, anchorAt.row) : null;
      if (!spawn) {
        spawn =
          this.walkableTiles.length > 0
            ? this.walkableTiles[Math.floor(Math.random() * this.walkableTiles.length)]
            : { col: 1, row: 1 };
      }
      ch = createCharacter(id, palette, null, null, hueShift);
      ch.x = spawn.col * TILE_SIZE + TILE_SIZE / 2;
      ch.y = spawn.row * TILE_SIZE + TILE_SIZE / 2;
      ch.tileCol = spawn.col;
      ch.tileRow = spawn.row;
    }

    if (folderName) {
      ch.folderName = folderName;
    }
    if (!skipSpawnEffect) {
      this.startEffect(ch, 'spawn');
    }
    this.characters.set(id, ch);
  }

  /** Every spawn/despawn starts here. Under REDUCED MOTION the transition SETTLES instead of
   *  animating — never skipped, never suspended: a spawn is visible in the same tick (any
   *  running effect cleared, the restored-agent path), and a despawn starts already complete,
   *  so the next update removes the character through the one canonical completion path,
   *  before any frame can draw it. */
  private startEffect(ch: Character, kind: 'spawn' | 'despawn'): void {
    if (this.reducedMotion && kind === 'spawn') {
      ch.matrixEffect = null;
      ch.matrixEffectTimer = 0;
      ch.matrixEffectSeeds = [];
      return;
    }
    startMatrixEffect(ch, kind);
    if (this.reducedMotion) ch.matrixEffectTimer = MATRIX_EFFECT_DURATION;
  }

  // ── Greeter ───────────────────────────────────────────────────
  // The Intro is diegetic: a char_0 character stands near the office's
  // bottom-left corner and "speaks" the tour through a DOM bubble
  // (IntroBubble). It is not an agent — see the `greeter` field.

  /** Spawn the greeter near the office's bottom-left corner: target tile
   *  GREETER_TILE_MARGIN in from the left and bottom edges, falling
   *  back to the closest walkable tile when the target is a seat, furniture,
   *  a wall, or VOID (seat tiles are in blockedTiles, so closestFreeWalkableTile
   *  covers every one of those). Idempotent; a remount mid-despawn (StrictMode)
   *  revives it. */
  spawnGreeter(): void {
    this.greeterCameraCancelled = false;
    if (this.greeter) {
      if (this.greeter.matrixEffect === 'despawn') this.startEffect(this.greeter, 'spawn');
      return;
    }
    const spawn = this.closestFreeWalkableTile(
      GREETER_TILE_MARGIN,
      this.layout.rows - 1 - GREETER_TILE_MARGIN,
    );
    if (!spawn) return; // no walkable tile — IntroBubble falls back to a fixed panel
    const ch = createCharacter(GREETER_ID, 0, null, null, 0);
    ch.isGreeter = true;
    ch.state = CharacterState.IDLE;
    ch.isActive = false;
    ch.dir = Direction.DOWN;
    ch.x = spawn.col * TILE_SIZE + TILE_SIZE / 2;
    ch.y = spawn.row * TILE_SIZE + TILE_SIZE / 2;
    ch.tileCol = spawn.col;
    ch.tileRow = spawn.row;
    this.startEffect(ch, 'spawn');
    this.greeter = ch;
  }

  /** Start the greeter's despawn effect and release the greeter camera. The
   *  character is dropped once the effect finishes (see update()).
   *  Idempotent — every close path (answer, Escape, hooksStatus) funnels here. */
  despawnGreeter(): void {
    this.greeterCameraTarget = null;
    this.greeterCameraCancelled = false;
    if (!this.greeter || this.greeter.matrixEffect === 'despawn') return;
    this.startEffect(this.greeter, 'despawn');
  }

  /** Per-frame update from the bubble overlay; ignored once the user panned. */
  setGreeterCameraTarget(p: { x: number; y: number }): void {
    if (!this.greeterCameraCancelled) this.greeterCameraTarget = p;
  }

  /** Manual pan during the ask: stop re-centering until the next spawn. */
  cancelGreeterCamera(): void {
    this.greeterCameraTarget = null;
    this.greeterCameraCancelled = true;
  }

  removeAgent(id: number): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    if (ch.matrixEffect === 'despawn') return; // already despawning
    // Free seat and clear selection immediately
    if (ch.seatId) {
      const seat = this.seats.get(ch.seatId);
      if (seat) seat.assigned = false;
    }
    if (this.selectedAgentId === id) this.selectedAgentId = null;
    if (this.cameraFollowId === id) this.cameraFollowId = null;
    // Start despawn animation instead of immediate delete
    this.startEffect(ch, 'despawn');
    ch.bubbleType = null;
  }

  /** Find seat uid at a given tile position, or null */
  getSeatAtTile(col: number, row: number): string | null {
    for (const [uid, seat] of this.seats) {
      if (seat.seatCol === col && seat.seatRow === row) return uid;
    }
    return null;
  }

  /** Reassign an agent from their current seat to a new seat */
  reassignSeat(agentId: number, seatId: string): void {
    const ch = this.characters.get(agentId);
    if (!ch) return;
    // Unassign old seat
    if (ch.seatId) {
      const old = this.seats.get(ch.seatId);
      if (old) old.assigned = false;
    }
    // Assign new seat
    const seat = this.seats.get(seatId);
    if (!seat || seat.assigned) return;
    seat.assigned = true;
    ch.seatId = seatId;
    // Pathfind to new seat (unblock own seat tile for this query)
    const path = this.withOwnSeatUnblocked(ch, () =>
      findPath(ch.tileCol, ch.tileRow, seat.seatCol, seat.seatRow, this.tileMap, this.blockedTiles),
    );
    if (path.length > 0) {
      ch.path = path;
      ch.moveProgress = 0;
      ch.state = CharacterState.WALK;
      ch.frame = 0;
      ch.frameTimer = 0;
    } else {
      // Already at seat or no path — sit down
      ch.state = CharacterState.TYPE;
      ch.dir = seat.facingDir;
      ch.frame = 0;
      ch.frameTimer = 0;
      if (!ch.isActive) {
        ch.seatTimer = INACTIVE_SEAT_TIMER_MIN_SEC + Math.random() * INACTIVE_SEAT_TIMER_RANGE_SEC;
      }
    }
  }

  /**
   * DI seating: a character GIVES UP its seat. It stays the same character (id, selection, activity, look,
   * zone) and stands on the nearest free walkable tile, seatless like any character that arrived to none.
   * Returns the freed seat's uid, or null when it held none. Called only by the DI reconciliation
   * (`reconcileDiSeats`); the generic seating (`addAgent`, `findFreeSeat`, `rebuildFromLayout`) is unchanged.
   */
  vacateSeat(agentId: number): string | null {
    const ch = this.characters.get(agentId);
    if (!ch || ch.isSubagent || !ch.seatId) return null;
    const uid = ch.seatId;
    const seat = this.seats.get(uid);
    if (seat) seat.assigned = false;
    ch.seatId = null;
    const spot = this.closestFreeWalkableTile(ch.tileCol, ch.tileRow);
    if (spot) {
      ch.tileCol = spot.col;
      ch.tileRow = spot.row;
      ch.x = spot.col * TILE_SIZE + TILE_SIZE / 2;
      ch.y = spot.row * TILE_SIZE + TILE_SIZE / 2;
    }
    ch.path = [];
    ch.moveProgress = 0;
    ch.state = CharacterState.IDLE;
    ch.frame = 0;
    ch.frameTimer = 0;
    ch.wanderTimer = 0;
    ch.wanderCount = 0;
    return uid;
  }

  /** A free workstation of any detected office (office order, then the usual PC-bias pick), or null: no detection, or none is free. */
  private freeWorkstationPick(): string | null {
    const detection = this.officeDetection;
    if (!detection) return null;
    const electronics = this.buildElectronicsTileSet();
    for (const office of detection.offices) {
      const free = office.workstationIds.filter((uid) => this.seats.get(uid)?.assigned === false);
      if (free.length > 0) return this.pickFromSeats(free, electronics);
    }
    return null;
  }

  /** The first free table seat (desk-facing, no electronics) in office order then seat order, or null: no detection, or none is free. */
  private freeTablePick(): string | null {
    const detection = this.officeDetection;
    if (!detection) return null;
    for (const office of detection.offices) {
      const free = office.tableIds.find((uid) => this.seats.get(uid)?.assigned === false);
      if (free !== undefined) return free;
    }
    return null;
  }

  /**
   * DI seating: seat a character that holds none in a FREE seat (its zone, then an unzoned seat, then any, as
   * `findFreeSeat` orders them) and walk it there. False when it already has a seat or none is free.
   */
  seatIfFree(agentId: number): boolean {
    const ch = this.characters.get(agentId);
    if (!ch || ch.isSubagent || ch.seatId) return false;
    // with the three offices detected a deskless character takes a free WORKSTATION first, then a free table seat (no monitor), and an other chair
    // only when none of those is free anywhere
    const seatId =
      this.freeWorkstationPick() ?? this.freeTablePick() ?? this.findFreeSeat(ch.folderName);
    if (!seatId) return false;
    this.reassignSeat(agentId, seatId);
    return ch.seatId === seatId;
  }

  /**
   * Move a just-linked teammate to the free seat closest to its lead, so teams
   * cluster. Only moves when that seat is strictly closer than the teammate's
   * current one — a teammate created as a plain external agent (seated by an
   * arbitrary findFreeSeat) and tagged as a teammate only after tag discovery
   * would otherwise keep its arbitrary seat, unlike an inline teammate seated
   * next to the lead at creation.
   */
  private reseatNextToLead(teammateId: number, leadId: number): void {
    const teammate = this.characters.get(teammateId);
    const lead = this.characters.get(leadId);
    if (!teammate || !lead) return;
    const anchorAt = anchorTile(lead, this.seats);
    if (!anchorAt) return;
    const target = closestFreeSeat(this.seats, anchorAt.col, anchorAt.row);
    if (!target || target === teammate.seatId) return;
    const targetSeat = this.seats.get(target)!;
    const targetDist =
      Math.abs(targetSeat.seatCol - anchorAt.col) + Math.abs(targetSeat.seatRow - anchorAt.row);
    const currentSeat = teammate.seatId ? this.seats.get(teammate.seatId) : undefined;
    const currentDist = currentSeat
      ? Math.abs(currentSeat.seatCol - anchorAt.col) + Math.abs(currentSeat.seatRow - anchorAt.row)
      : Infinity;
    if (targetDist < currentDist) {
      this.reassignSeat(teammateId, target);
    }
  }

  /** Send an agent back to their currently assigned seat */
  sendToSeat(agentId: number): void {
    const ch = this.characters.get(agentId);
    if (!ch || !ch.seatId) return;
    const seat = this.seats.get(ch.seatId);
    if (!seat) return;
    const path = this.withOwnSeatUnblocked(ch, () =>
      findPath(ch.tileCol, ch.tileRow, seat.seatCol, seat.seatRow, this.tileMap, this.blockedTiles),
    );
    if (path.length > 0) {
      ch.path = path;
      ch.moveProgress = 0;
      ch.state = CharacterState.WALK;
      ch.frame = 0;
      ch.frameTimer = 0;
    } else {
      // Already at seat — sit down
      ch.state = CharacterState.TYPE;
      ch.dir = seat.facingDir;
      ch.frame = 0;
      ch.frameTimer = 0;
      if (!ch.isActive) {
        ch.seatTimer = INACTIVE_SEAT_TIMER_MIN_SEC + Math.random() * INACTIVE_SEAT_TIMER_RANGE_SEC;
      }
    }
  }

  /** Walk an agent to an arbitrary walkable tile (right-click command) */
  walkToTile(agentId: number, col: number, row: number): boolean {
    const ch = this.characters.get(agentId);
    if (!ch || ch.isSubagent) return false;
    if (!isWalkable(col, row, this.tileMap, this.blockedTiles)) {
      // Also allow walking to own seat tile (blocked for others but not self)
      const key = this.ownSeatKey(ch);
      if (!key || key !== `${col},${row}`) return false;
    }
    const path = this.withOwnSeatUnblocked(ch, () =>
      findPath(ch.tileCol, ch.tileRow, col, row, this.tileMap, this.blockedTiles),
    );
    if (path.length === 0) return false;
    ch.path = path;
    ch.moveProgress = 0;
    ch.state = CharacterState.WALK;
    ch.frame = 0;
    ch.frameTimer = 0;
    return true;
  }

  /** Create a sub-agent character with the parent's palette. Returns the sub-agent ID. */
  addSubagent(parentAgentId: number, parentToolId: string): number {
    const key = `${parentAgentId}:${parentToolId}`;
    if (this.subagentIdMap.has(key)) return this.subagentIdMap.get(key)!;

    const id = this.nextSubagentId--;
    const parentCh = this.characters.get(parentAgentId);
    const palette = parentCh ? parentCh.palette : 0;
    const hueShift = parentCh ? parentCh.hueShift : 0;

    // Find the closest walkable tile to the parent, avoiding tiles occupied by other characters
    const parentCol = parentCh ? parentCh.tileCol : 0;
    const parentRow = parentCh ? parentCh.tileRow : 0;
    let spawn = { col: parentCol, row: parentRow };
    if (this.walkableTiles.length > 0) {
      spawn = this.closestFreeWalkableTile(parentCol, parentRow) ?? this.walkableTiles[0];
    }

    const ch = createCharacter(id, palette, null, null, hueShift);
    ch.x = spawn.col * TILE_SIZE + TILE_SIZE / 2;
    ch.y = spawn.row * TILE_SIZE + TILE_SIZE / 2;
    ch.tileCol = spawn.col;
    ch.tileRow = spawn.row;
    // Face the same direction as the parent agent
    if (parentCh) ch.dir = parentCh.dir;
    ch.isSubagent = true;
    ch.parentAgentId = parentAgentId;
    this.startEffect(ch, 'spawn');
    this.characters.set(id, ch);

    this.subagentIdMap.set(key, id);
    this.subagentMeta.set(id, { parentAgentId, parentToolId });
    return id;
  }

  /** Remove a specific sub-agent character and free its seat */
  removeSubagent(parentAgentId: number, parentToolId: string): void {
    const key = `${parentAgentId}:${parentToolId}`;
    const id = this.subagentIdMap.get(key);
    if (id === undefined) return;

    const ch = this.characters.get(id);
    if (ch) {
      if (ch.matrixEffect === 'despawn') {
        // Already despawning — just clean up maps
        this.subagentIdMap.delete(key);
        this.subagentMeta.delete(id);
        return;
      }
      if (ch.seatId) {
        const seat = this.seats.get(ch.seatId);
        if (seat) seat.assigned = false;
      }
      // Start despawn animation — keep character in map for rendering
      this.startEffect(ch, 'despawn');
      ch.bubbleType = null;
    }
    // Clean up tracking maps immediately so keys don't collide
    this.subagentIdMap.delete(key);
    this.subagentMeta.delete(id);
    if (this.selectedAgentId === id) this.selectedAgentId = null;
    if (this.cameraFollowId === id) this.cameraFollowId = null;
  }

  /** Remove all sub-agents belonging to a parent agent */
  removeAllSubagents(parentAgentId: number): void {
    const toRemove: string[] = [];
    for (const [key, id] of this.subagentIdMap) {
      const meta = this.subagentMeta.get(id);
      if (meta && meta.parentAgentId === parentAgentId) {
        const ch = this.characters.get(id);
        if (ch) {
          if (ch.matrixEffect === 'despawn') {
            // Already despawning — just clean up maps
            this.subagentMeta.delete(id);
            toRemove.push(key);
            continue;
          }
          if (ch.seatId) {
            const seat = this.seats.get(ch.seatId);
            if (seat) seat.assigned = false;
          }
          // Start despawn animation
          this.startEffect(ch, 'despawn');
          ch.bubbleType = null;
        }
        this.subagentMeta.delete(id);
        if (this.selectedAgentId === id) this.selectedAgentId = null;
        if (this.cameraFollowId === id) this.cameraFollowId = null;
        toRemove.push(key);
      }
    }
    for (const key of toRemove) {
      this.subagentIdMap.delete(key);
    }
  }

  /** Look up the sub-agent character ID for a given parent+toolId, or null */
  getSubagentId(parentAgentId: number, parentToolId: string): number | null {
    return this.subagentIdMap.get(`${parentAgentId}:${parentToolId}`) ?? null;
  }

  setAgentActive(id: number, active: boolean): void {
    const ch = this.characters.get(id);
    if (ch) {
      const changed = ch.isActive !== active;
      ch.isActive = active;
      if (!active) {
        // Sentinel -1: signals turn just ended, skip next seat rest timer.
        // Prevents the WALK handler from setting a 2-4 min rest on arrival.
        ch.seatTimer = -1;
        ch.path = [];
        ch.moveProgress = 0;
      }
      // The auto-state ON/OFF swap depends only on who is active: an unchanged state (the DI
      // office re-asserts every member's activity on each refresh) rebuilds nothing.
      if (changed) this.rebuildFurnitureInstances();
    }
  }

  /** Rebuild furniture instances with auto-state applied (active agents turn electronics ON) */
  /** Recompute every furniture instance's sprite (as a layout rebuild does): the e2e render-cache
   *  probe uses it to re-render right after a reset, with nothing kept from before. */
  refreshFurnitureSprites(): void {
    this.rebuildFurnitureInstances();
  }

  private rebuildFurnitureInstances(animationFrameOnly = false): void {
    if (animationFrameOnly && this.furnitureAnimSlots !== null) {
      this.applyFurnitureAnimFrame();
      return;
    }
    if (!animationFrameOnly) this.furnitureStaticVersion++;
    // Collect tiles where active agents face desks
    const autoOnTiles = new Set<string>();
    for (const ch of this.characters.values()) {
      if (!ch.isActive || !ch.seatId) continue;
      const seat = this.seats.get(ch.seatId);
      if (!seat) continue;
      // Find the desk tile(s) the agent faces from their seat
      const dCol =
        seat.facingDir === Direction.RIGHT ? 1 : seat.facingDir === Direction.LEFT ? -1 : 0;
      const dRow = seat.facingDir === Direction.DOWN ? 1 : seat.facingDir === Direction.UP ? -1 : 0;
      // Check tiles in the facing direction (desk could be 1-3 tiles deep)
      for (let d = 1; d <= AUTO_ON_FACING_DEPTH; d++) {
        const tileCol = seat.seatCol + dCol * d;
        const tileRow = seat.seatRow + dRow * d;
        autoOnTiles.add(`${tileCol},${tileRow}`);
      }
      // Also check tiles to the sides of the facing direction (desks can be wide)
      for (let d = 1; d <= AUTO_ON_SIDE_DEPTH; d++) {
        const baseCol = seat.seatCol + dCol * d;
        const baseRow = seat.seatRow + dRow * d;
        if (dCol !== 0) {
          // Facing left/right: check tiles above and below
          autoOnTiles.add(`${baseCol},${baseRow - 1}`);
          autoOnTiles.add(`${baseCol},${baseRow + 1}`);
        } else {
          // Facing up/down: check tiles left and right
          autoOnTiles.add(`${baseCol - 1},${baseRow}`);
          autoOnTiles.add(`${baseCol + 1},${baseRow}`);
        }
      }
    }

    // A vehicle that is driving is drawn by the traffic, not as furniture: its static copy is skipped here.
    // The LAYOUT is untouched (and with traffic off nothing is skipped), so 'off' is the world exactly as it was.
    const source = this.visibleFurniture();
    const animFrame = Math.floor(this.furnitureAnimTimer / FURNITURE_ANIM_INTERVAL_SEC);
    let rendered = source;
    if (autoOnTiles.size > 0) {
      // Build modified furniture list with auto-state and animation applied
      rendered = source.map((item) => {
        const entry = getCatalogEntry(item.type);
        if (!entry) return item;
        // Check if any tile of this furniture overlaps an auto-on tile
        for (let dr = 0; dr < entry.footprintH; dr++) {
          for (let dc = 0; dc < entry.footprintW; dc++) {
            if (autoOnTiles.has(`${item.col + dc},${item.row + dr}`)) {
              let onType = getOnStateType(item.type);
              if (onType !== item.type) {
                // Check if the on-state type has animation frames
                const frames = getAnimationFrames(onType);
                if (frames && frames.length > 1) {
                  const frameIdx = animFrame % frames.length;
                  onType = frames[frameIdx];
                }
                return { ...item, type: onType };
              }
              return item;
            }
          }
        }
        return item;
      });
    }

    this.furniture = layoutToFurnitureInstances(rendered);
    this.furnitureAnimSlots = this.indexFurnitureAnim(rendered);
  }

  /** Instance index matches layoutToFurnitureInstances, which skips a type the catalog does not know. */
  private indexFurnitureAnim(items: PlacedFurniture[]): { index: number; sprites: SpriteData[] }[] {
    const slots: { index: number; sprites: SpriteData[] }[] = [];
    let index = 0;
    for (const item of items) {
      if (!getCatalogEntry(item.type)) continue;
      const frames = getAnimationFrames(item.type);
      if (frames && frames.length > 1) {
        const sprites: SpriteData[] = [];
        for (const type of frames) {
          const frameEntry = getCatalogEntry(type);
          if (!frameEntry) continue;
          sprites.push(this.furnitureSprite(item, type, frameEntry.sprite));
        }
        if (sprites.length > 1) slots.push({ index, sprites });
      }
      index++;
    }
    return slots;
  }

  private furnitureSprite(item: PlacedFurniture, type: string, base: SpriteData): SpriteData {
    if (!item.color) return base;
    const { h, s, b: bv, c: cv } = item.color;
    return getColorizedSprite(
      `furn-${type}-${h}-${s}-${bv}-${cv}-${item.color.colorize ? 1 : 0}`,
      base,
      item.color,
    );
  }

  private applyFurnitureAnimFrame(): void {
    const slots = this.furnitureAnimSlots;
    if (!slots || slots.length === 0) return;
    const frame = Math.floor(this.furnitureAnimTimer / FURNITURE_ANIM_INTERVAL_SEC);
    for (const slot of slots) {
      const inst = this.furniture[slot.index];
      if (!inst) continue;
      const sprite = slot.sprites[frame % slot.sprites.length];
      if (inst.sprite !== sprite) inst.sprite = sprite;
    }
  }

  setAgentTool(id: number, tool: string | null): void {
    const ch = this.characters.get(id);
    if (ch) {
      ch.currentTool = tool;
    }
  }

  showPermissionBubble(id: number): void {
    const ch = this.characters.get(id);
    if (ch) {
      ch.bubbleType = 'permission';
      ch.bubbleTimer = 0;
    }
  }

  clearPermissionBubble(id: number): void {
    const ch = this.characters.get(id);
    if (ch && ch.bubbleType === 'permission') {
      ch.bubbleType = null;
      ch.bubbleTimer = 0;
    }
  }

  showWaitingBubble(id: number, awaitingInput = false): void {
    const ch = this.characters.get(id);
    if (ch) {
      ch.bubbleType = 'waiting';
      ch.waitingAwaitingInput = awaitingInput;
      ch.bubbleTimer = WAITING_BUBBLE_DURATION_SEC;
    }
  }

  /** Dismiss bubble on click — permission: instant, waiting: quick fade */
  dismissBubble(id: number): void {
    const ch = this.characters.get(id);
    if (!ch || !ch.bubbleType) return;
    if (ch.bubbleType === 'permission') {
      ch.bubbleType = null;
      ch.bubbleTimer = 0;
    } else if (ch.bubbleType === 'waiting') {
      // Trigger immediate fade (0.3s remaining)
      ch.bubbleTimer = Math.min(ch.bubbleTimer, DISMISS_BUBBLE_FAST_FADE_SEC);
    }
  }

  // ── Pets ──────────────────────────────────────────────────────

  /**
   * Add a pet to the live runtime. Spawns at a uniformly-random walkable tile.
   * Mirror in `this.layout.pets` so debounced saveLayout serialises the roster.
   * Bounds-checks petType against the loaded sprite count to defend against stale layouts.
   */
  addPet(placedPet: PlacedPet): void {
    // Defensive guards (upstream 5e6c0a0)
    if (
      typeof placedPet.id !== 'string' ||
      placedPet.id.length === 0 ||
      placedPet.id.length > MAX_PET_ID_LENGTH
    ) {
      return;
    }
    if (
      !Number.isInteger(placedPet.petType) ||
      placedPet.petType < 0 ||
      placedPet.petType >= getPetCount()
    ) {
      return;
    }
    if (this.pets.some((p) => p.id === placedPet.id)) return; // de-dupe
    if (this.walkableTiles.length === 0) return; // no spawn space — silently drop

    const spawn = this.walkableTiles[Math.floor(Math.random() * this.walkableTiles.length)];
    const pet = createPet(placedPet.id, placedPet.petType, spawn.col, spawn.row);
    pet.name = getPetName(placedPet.petType);
    this.pets.push(pet);
    this.syncLayoutPets();
  }

  /** Remove a pet by id. Idempotent. */
  removePet(id: string): void {
    const before = this.pets.length;
    this.pets = this.pets.filter((p) => p.id !== id);
    if (this.pets.length !== before) {
      this.syncLayoutPets();
    }
  }

  /** Shallow snapshot for external consumers (renderer, hooks). */
  getPets(): Pet[] {
    return this.pets.slice();
  }

  /** Unique petType values currently placed. Used by the Pets toolbar to mark active rows. */
  getActivePetTypes(): number[] {
    const seen = new Set<number>();
    for (const p of this.pets) seen.add(p.petType);
    return Array.from(seen);
  }

  /**
   * Hit-test pets at a pixel world position. Sorts back-to-front (largest y wins on tie)
   * so the visually-frontmost pet receives the click.
   * Returns the pet id or null.
   */
  getPetAt(worldX: number, worldY: number): string | null {
    const ordered = this.pets.slice().sort((a, b) => b.y - a.y);
    for (const pet of ordered) {
      const left = pet.x - PET_HIT_HALF_WIDTH;
      const right = pet.x + PET_HIT_HALF_WIDTH;
      const top = pet.y - PET_HIT_HEIGHT;
      const bottom = pet.y;
      if (worldX >= left && worldX <= right && worldY >= top && worldY <= bottom) {
        return pet.id;
      }
    }
    return null;
  }

  /** Show the heart bubble on a pet for WAITING_BUBBLE_DURATION_SEC. */
  showPetBubble(petId: string): void {
    const pet = this.pets.find((p) => p.id === petId);
    if (!pet) return;
    pet.bubbleType = 'heart';
    pet.bubbleTimer = WAITING_BUBBLE_DURATION_SEC;
  }

  /** Dismiss the heart bubble on click; collapses timer to a fast fade. */
  dismissPetBubble(petId: string): void {
    const pet = this.pets.find((p) => p.id === petId);
    if (!pet || !pet.bubbleType) return;
    pet.bubbleTimer = Math.min(pet.bubbleTimer, DISMISS_BUBBLE_FAST_FADE_SEC);
  }

  /**
   * Reconcile `this.pets` to match the layout's placed-pet roster.
   * - Pets in layout but not in runtime → spawn via addPet().
   * - Pets in runtime but not in layout → remove.
   * - Pets in both → keep existing runtime state (position, FSM).
   *
   * Called from constructor and rebuildFromLayout. Always runs AFTER walkableTiles
   * is populated.
   */
  private rebuildPetsFromLayout(layout: OfficeLayout): void {
    const placed = layout.pets ?? [];
    const placedIds = new Set(placed.map((p) => p.id));

    // 1. Remove pets no longer in layout
    this.pets = this.pets.filter((p) => placedIds.has(p.id));

    // 2. Add pets that exist in layout but not in runtime
    const existingIds = new Set(this.pets.map((p) => p.id));
    for (const p of placed) {
      if (existingIds.has(p.id)) continue;
      this.addPet(p); // pushes onto this.pets, calls syncLayoutPets()
    }
    // syncLayoutPets() inside addPet keeps this.layout.pets coherent; one final
    // sync handles the removal-only branch where addPet was never called.
    this.syncLayoutPets();
  }

  /**
   * Re-export the current pet roster into `this.layout.pets`. Called only from
   * mutating methods (addPet / removePet / rebuildPetsFromLayout) — NEVER from
   * getLayout(), which runs on every render frame.
   */
  private syncLayoutPets(): void {
    this.layout.pets = this.pets.map((p) => ({ id: p.id, petType: p.petType }));
  }

  setTeamInfo(
    id: number,
    teamName?: string,
    agentName?: string,
    isTeamLead?: boolean,
    leadAgentId?: number,
    teamUsesTmux?: boolean,
  ): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    const wasUnlinked = ch.leadAgentId === undefined;
    ch.teamName = teamName;
    ch.agentName = agentName;
    ch.isTeamLead = isTeamLead;
    ch.leadAgentId = leadAgentId;
    if (teamUsesTmux !== undefined) {
      ch.teamUsesTmux = teamUsesTmux;
    }
    // A teammate is not a headless agent: clicking it focuses its lead's terminal.
    // Adopted sessions are marked headless at creation and only later discovered
    // to be teammates, so drop the mark once the link lands.
    if (leadAgentId !== undefined) {
      ch.isHeadless = false;
    }
    // A teammate discovered only after its plain external session was adopted is
    // linked here, not at creation, so it never went through the seat-next-to-lead
    // path addAgent runs for inline teammates. Cluster it now, once, on first link.
    if (wasUnlinked && leadAgentId !== undefined && !isTeamLead) {
      this.reseatNextToLead(id, leadAgentId);
    }
  }

  /** Mark an agent as headless (adopted, no terminal to focus). */
  setHeadless(id: number, headless: boolean): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    ch.isHeadless = headless;
  }

  setAgentContext(id: number, contextTokens: number, maxContextTokens: number): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    ch.contextTokens = contextTokens;
    ch.maxContextTokens = maxContextTokens;
  }

  update(dt: number): void {
    // The city's lighting follows its target; weather moves unless motion is reduced. The game loop's
    // hidden guard means neither advances while the view is hidden.
    this.city.update(dt, Date.now(), this.reducedMotion);

    // Furniture animation cycling (held on one frame under reduced motion; ON/OFF auto-state
    // still follows activity, rebuilt by setAgentActive)
    if (!this.reducedMotion) {
      const prevFrame = Math.floor(this.furnitureAnimTimer / FURNITURE_ANIM_INTERVAL_SEC);
      this.furnitureAnimTimer += dt;
      const newFrame = Math.floor(this.furnitureAnimTimer / FURNITURE_ANIM_INTERVAL_SEC);
      if (newFrame !== prevFrame) {
        this.rebuildFurnitureInstances(true);
      }
    }

    // The greeter materializes and dematerializes like anyone else, but runs
    // no FSM — it stands where it spawned for as long as the ask is up.
    // Reduced motion also SETTLES effects already in flight when it is enabled: completed, never
    // suspended — a reveal finishes, and a despawn finishes and so removes the character.
    if (this.reducedMotion && this.greeter?.matrixEffect) {
      this.greeter.matrixEffectTimer = MATRIX_EFFECT_DURATION;
    }
    if (this.greeter && advanceMatrixEffect(this.greeter, dt) === 'despawned') {
      this.greeter = null;
    }

    const toDelete: number[] = [];
    for (const ch of this.characters.values()) {
      if (this.reducedMotion && ch.matrixEffect) ch.matrixEffectTimer = MATRIX_EFFECT_DURATION;
      const effect = advanceMatrixEffect(ch, dt);
      if (effect !== 'none') {
        if (effect === 'despawned') toDelete.push(ch.id);
        continue; // skip normal FSM while the effect is (or just was) active
      }

      if (this.reducedMotion) {
        // No autonomous motion: at its seat, one held frame (activity read, never written)
        settleCharacter(ch, this.seats);
      } else {
        // Temporarily unblock own seat so character can pathfind to it
        this.withOwnSeatUnblocked(ch, () =>
          updateCharacter(ch, dt, this.walkableTiles, this.seats, this.tileMap, this.blockedTiles),
        );
      }

      // Tick bubble timer for waiting bubbles
      if (ch.bubbleType === 'waiting') {
        ch.bubbleTimer -= dt;
        if (ch.bubbleTimer <= 0) {
          ch.bubbleType = null;
          ch.bubbleTimer = 0;
        }
      }
    }
    // Remove characters that finished despawn
    for (const id of toDelete) {
      this.characters.delete(id);
    }

    // ── Ambient residents ──────────────────────────────────────
    // Decorative MOTION holds still under reduced motion, like pets; real-agent PRIORITY never does:
    // a resident on an agent's tile or an assigned seat's zone still yields (placed at once, no
    // animation; AmbientWorld.yieldWithoutMotion). Reduced motion holds motion, never priority.
    if (this.ambient) {
      if (!this.reducedMotion) {
        this.ambient.update(dt, this.ambientAgentContext());
        this.ambientClock += dt;
      } else {
        this.ambient.yieldWithoutMotion(this.ambientAgentContext());
      }
    }
    // The subway holds still under reduced motion too (decorative motion)
    if (this.transit && !this.reducedMotion) this.transit.update(dt);

    // ── Decorative traffic ─────────────────────────────────────
    // Motion holds still under reduced motion; priority never does (a vehicle still gives way to anyone
    // who steps into it). It reads where people are and writes nothing to them.
    if (this.traffic && this.traffic.activeCap > 0) {
      this.traffic.update(dt, this.gatherTrafficPeople(), this.reducedMotion);
    }

    // ── Pet FSM ────────────────────────────────────────────────
    for (const pet of this.pets) {
      // Pets are decoration: they hold still under reduced motion (their bubbles still tick)
      if (!this.reducedMotion) {
        updatePet(pet, dt, this.walkableTiles, this.characters, this.tileMap, this.blockedTiles);
      }

      // Tick heart bubble timer (mirrors character waiting-bubble pattern)
      if (pet.bubbleType) {
        pet.bubbleTimer -= dt;
        if (pet.bubbleTimer <= 0) {
          pet.bubbleType = null;
          pet.bubbleTimer = 0;
        }
      }
    }
  }

  /** Turn the reception desk's receptionist on (DI mode) or off. Idempotent. */
  setReceptionistEnabled(enabled: boolean): void {
    if (this.receptionistEnabled === enabled) return;
    this.receptionistEnabled = enabled;
    // the post, its reserved workstation, the occupied tile and the three-office detection are all derived from the layout while the DI
    // office is on: re-derive them (a layout with none of them changes nothing here), and keep characters on valid seats
    const before = this.seatingKey();
    this.deriveSeating(this.layout);
    this.walkableTiles = livingTiles(
      this.layout,
      this.tileMap,
      this.blockedTiles,
      this.seats,
      this.officeDetection,
    );
    this.reseatCharacters();
    this.rebuildReceptionist();
    // the residents' plan reads the post and the offices: rebuild them only if those changed
    if (this.ambient && before !== this.seatingKey()) this.rebuildAmbient();
  }

  /** What the ambient plan reads from the seating derivation (a change means the residents are rebuilt). */
  private seatingKey(): string {
    const post = this.receptionPost;
    return JSON.stringify([
      post ? [post.standCol, post.standRow, post.tileBlocked] : null,
      this.officeDetection?.offices.map((o) => [o.label, o.seatIds.length, o.workstationIds.length + o.tableIds.length]) ?? null,
    ]);
  }

  /** Derive the receptionist from the loaded layout: standing at its post, facing the desk, present
   *  at once (no spawn effect: reduced motion and a reload see the same thing). Null when disabled
   *  or when the layout has no reception desk. */
  private rebuildReceptionist(): void {
    this.receptionist = null;
    const post = this.receptionPost; // derived with the seating (deriveSeating)
    if (!post) return;
    const ch = createCharacter(DI_RECEPTIONIST_ID, DI_RECEPTIONIST_PALETTE, null, null, 0);
    ch.isReceptionist = true;
    ch.state = CharacterState.IDLE;
    ch.isActive = false;
    ch.dir = Direction.DOWN;
    ch.x = post.standCol * TILE_SIZE + TILE_SIZE / 2;
    ch.y = post.standRow * TILE_SIZE + TILE_SIZE / 2;
    ch.tileCol = post.standCol;
    ch.tileRow = post.standRow;
    this.receptionist = ch;
  }

  /** Turn the ambient population on (DI mode) or off. Idempotent. */
  setAmbientEnabled(enabled: boolean): void {
    if (this.ambientEnabled === enabled && (enabled ? this.ambient !== null : this.ambient === null))
      return;
    this.ambientEnabled = enabled;
    this.rebuildAmbient();
    this.syncTraffic();
  }

  private rebuildAmbient(): void {
    this.hoveredAmbientId = null;
    // The residents' mask never blocks the receptionist's stand tile: residents already give way to it like to an agent's, and no plan
    // targets it (`postTile`). That keeps every remote resident's plan identical to the previous build.
    const walkBlocked = this.ambientBlockedTiles();
    const line = this.ambientEnabled
      ? deriveTransitLine(this.layout, (c, r) => isWalkable(c, r, this.tileMap, walkBlocked))
      : null;
    this.ambient = this.ambientEnabled
      ? buildAmbientWorld(this.ambientBuildInput(walkBlocked, line))
      : null;
    this.transit = this.ambient && line ? new TransitWorld(line, this.ambient) : null;
    // a saved snapshot applies ONCE, to the first world built after it arrived, and only if its world
    // fingerprint matches (another layout or an import discards it): never a duplicate, never a
    // second application
    if (this.ambient && this.pendingAmbientSnapshot !== null) this.tryPendingSnapshot();
  }

  /** `blockedTiles` as the residents' walk mask sees them: the furniture, without the receptionist's stand tile. */
  private ambientBlockedTiles(): Set<string> {
    if (!this.standTileKey) return this.blockedTiles;
    const blocked = new Set(this.blockedTiles);
    blocked.delete(this.standTileKey);
    return blocked;
  }

  /** The ambient world's inputs for THIS layout (the new plan). Without a reception post and without detected offices they are the previous build's. */
  private ambientBuildInput(
    walkBlocked: Set<string>,
    line: ReturnType<typeof deriveTransitLine>,
  ): Parameters<typeof buildAmbientWorld>[0] {
    const post = this.receptionPost;
    const detection = this.officeDetection;
    const cols = this.layout.cols;
    // seat zones: the eligible seats, plus the receptionist's own workstation (residents keep clear of it as they always did)
    const zoneSeats = detection
      ? [...this.seats.values(), ...this.legacySeatList.filter((s) => this.reservedSeatIds.has(s.uid))]
      : this.legacySeatList;
    const input: Parameters<typeof buildAmbientWorld>[0] = {
      layout: this.layout,
      tileMap: this.tileMap,
      blockedTiles: walkBlocked,
      homeRegions: livingRegions(this.layout, footprintOf),
      seats: zoneSeats,
      transit: line,
    };
    if (post) {
      input.postedReceptionist = true;
      // The fixed-post route block exists only while the tile really is blocked. In the corridor fallback (`tileBlocked` false: blocking it would cut a
      // sole passage) it stays passable — residents may walk through and briefly overlap the decorative receptionist — so no plan or route avoids it.
      if (post.tileBlocked) input.postTile = post.standRow * cols + post.standCol;
    }
    if (detection) {
      const seatOf = (uid: string) => this.allSeatsByUid().get(uid);
      input.offices = {
        labels: detection.offices.map((o) => o.label),
        legacySeats: this.legacySeatList,
        // the residents' WORK rooms are the rooms holding a desk-facing seat — a workstation or a table seat — exactly the input they always had
        workSeats: detection.offices
          .flatMap((o) => [...o.workstationIds, ...o.tableIds])
          .map(seatOf)
          .filter((s): s is Seat => s !== undefined),
      };
    }
    return input;
  }

  /** Every eligible seat by uid (the seats the offices were detected over): a small helper for the residents' plan inputs. */
  private allSeatsByUid(): ReadonlyMap<string, Seat> {
    return this.seats;
  }

  /**
   * The PREDECESSOR residents (the previous build's plan for this exact layout): the same builder with the previous inputs — furniture-only
   * blocked tiles, the home seats, no post, no offices. Built only on demand, to recognise a snapshot saved by the previous build
   * (applyAmbientSnapshot's migration); null when the residents are off.
   */
  predecessorAmbient(): { world: AmbientWorld; transit: TransitWorld | null } | null {
    if (!this.ambientEnabled) return null;
    const blocked = getBlockedTiles(this.layout.furniture);
    const line = deriveTransitLine(this.layout, (c, r) => isWalkable(c, r, this.tileMap, blocked));
    const world = buildAmbientWorld({
      layout: this.layout,
      tileMap: this.tileMap,
      blockedTiles: blocked,
      homeRegions: livingRegions(this.layout, footprintOf),
      seats: this.legacySeatList,
      transit: line,
    });
    return { world, transit: line ? new TransitWorld(line, world) : null };
  }

  /** What residents must give way to this tick: every real agent's tile (and the tile it is about
   *  to step onto) and every seat assigned to a real agent. Read-only; agents never see residents. */
  private ambientAgentContext(): { agentTiles: Set<number>; assignedSeats: string[] } {
    const cols = this.layout.cols;
    const agentTiles = new Set<number>();
    for (const ch of this.characters.values()) {
      if (ch.matrixEffect === 'despawn') continue;
      agentTiles.add(ch.tileRow * cols + ch.tileCol);
      const next = ch.path[0];
      if (next) agentTiles.add(next.row * cols + next.col);
    }
    // the receptionist holds its post: residents give way to its tile like to an agent's — only while the tile is really blocked; in the corridor
    // fallback (`tileBlocked` false) it is a passage, so residents (and agents) may walk through and briefly overlap the decorative receptionist
    if (this.receptionist && this.receptionPost?.tileBlocked)
      agentTiles.add(this.receptionist.tileRow * cols + this.receptionist.tileCol);
    const assignedSeats: string[] = [];
    for (const [uid, seat] of this.seats) if (seat.assigned) assignedSeats.push(uid);
    return { agentTiles, assignedSeats };
  }

  /** Hold a saved snapshot (from the handshake) for the next ambient build; applies immediately if
   *  the residents already exist for this world. */
  setAmbientSnapshot(snapshot: unknown): void {
    if (snapshot === null || snapshot === undefined) return;
    this.pendingAmbientSnapshot = snapshot;
    this.ambientAttemptRecorded = false;
    this.ambientSaveWithheld = null;
    if (this.ambient) this.tryPendingSnapshot();
  }

  /** A user-IMPORTED snapshot: applied to the running world at once, or refused with a reason; it is
   *  never held for a later world (unlike the handshake snapshot, which may arrive before its world). */
  importAmbientSnapshot(snapshot: unknown): AmbientRestoreResult {
    const result: AmbientRestoreResult = this.ambient
      ? applyAmbientSnapshot(this.layout, this.ambient, this.transit, snapshot, () =>
          this.predecessorAmbient(),
        )
      : { outcome: 'refused', reason: 'the residents are not running in this view', residentsRestored: 0, aboardRestored: 0, waitingRestored: 0, trainsRestored: 0 };
    this.lastAmbientRestore = result;
    return result;
  }

  /** Apply the held snapshot: once applied (or refused) it is dropped; a snapshot for ANOTHER world
   *  stays held. The fingerprint guarantees it can only ever apply to the world it was saved from. */
  private tryPendingSnapshot(): void {
    if (!this.ambient || this.pendingAmbientSnapshot === null) return;
    const result = applyAmbientSnapshot(
      this.layout,
      this.ambient,
      this.transit,
      this.pendingAmbientSnapshot,
      () => this.predecessorAmbient(),
    );
    this.lastAmbientRestore = result;
    this.ambientAttemptRecorded = true;
    // a snapshot of the previous build's plan that cannot be migrated safely is left exactly as it is: nothing is saved over it this session
    if (result.outcome === 'refused' && result.reason?.startsWith('migration_unsupported:'))
      this.ambientSaveWithheld = result.reason;
    if (result.outcome !== 'other-world') this.pendingAmbientSnapshot = null;
  }

  /** The current snapshot (null while the residents are off). Pure: it does not decide whether it may be SAVED (see `serializeAmbientForSave`). */
  serializeAmbient(savedAt: number): AmbientSaveState | null {
    return this.ambient ? serializeAmbient(this.layout, this.ambient, this.transit, savedAt) : null;
  }

  /**
   * The snapshot to SAVE, or null when saving now could destroy a file nobody has read yet: a handshake snapshot is held and no restore attempt has
   * been recorded against it (`lastAmbientRestore`), or its migration was refused as unsupported (the file stays exactly as it is, for this session).
   * Once the attempt is recorded — applied, migrated, refused for any other reason, or a snapshot of another world — saving proceeds as always.
   */
  serializeAmbientForSave(savedAt: number): AmbientSaveState | null {
    if (!this.ambient || this.ambientSaveWithheld !== null) return null;
    if (this.pendingAmbientSnapshot !== null && !this.ambientAttemptRecorded) return null;
    return this.serializeAmbient(savedAt);
  }

  /** DI office only: the canonical identity key and how each real character was placed (set by the DI reconciliation; evidence, never state). */
  private readonly diPlacementNotes = new Map<
    number,
    { identityKey: string; placement: 'preserved' | 'fresh' | 'fallback' | 'unplanned' }
  >();

  /** Record who a DI character is and how its seat was decided. Read-only evidence for `officeAssignment`; it drives nothing. */
  noteDiPlacement(
    id: number,
    identityKey: string,
    placement: 'preserved' | 'fresh' | 'fallback' | 'unplanned',
  ): void {
    this.diPlacementNotes.set(id, { identityKey, placement });
  }

  /** How many identities sitting now did NOT fit the seat-preference record budget at the last reconcile (ordinarily 0). Evidence only. */
  private seatPreferencesOverBudget = 0;

  /** Record the over-budget count of the identity-keyed seat preferences (a counted condition surfaced in `officeAssignment`). */
  noteSeatPreferenceOverBudget(count: number): void {
    this.seatPreferencesOverBudget = count;
  }

  /** Forget the notes of characters that are no longer drawn. */
  pruneDiPlacementNotes(): void {
    for (const id of this.diPlacementNotes.keys()) if (!this.characters.has(id)) this.diPlacementNotes.delete(id);
  }

  /**
   * READ-ONLY EVIDENCE of the three-office seating: the detected offices (seats vs workstations, the seat lists), the receptionist's reserved seats,
   * and for every real character its identity, seat, office and what kind of seat it holds — a desk-facing seat with no electronics is reported as
   * 'table, not workstation', any other non-workstation seat as 'chair, not workstation'. Null while no offices are detected (legacy seating).
   */
  officeAssignment(): {
    offices: Array<{
      index: number;
      label: string;
      kind: 'home' | 'district';
      seats: number;
      workstations: number;
      tables: number;
      otherChairs: number;
      seatIds: string[];
      workstationIds: string[];
      tableIds: string[];
    }>;
    reservedSeatIds: string[];
    /** Identities sitting now that did not fit the seat-preference record budget (ordinarily 0; never dropped silently). */
    seatPreferencesOverBudget: number;
    characters: Array<{
      id: number;
      identityKey: string | null;
      seatId: string | null;
      office: string | null;
      seatKind: 'workstation' | 'table, not workstation' | 'chair, not workstation' | null;
      placement: 'preserved' | 'fresh' | 'fallback' | 'unplanned' | null;
    }>;
  } | null {
    const detection = this.officeDetection;
    if (!detection) return null;
    const characters = [...this.characters.values()]
      .filter((ch) => !ch.isSubagent)
      .map((ch) => {
        const seat = ch.seatId;
        const officeIndex = seat ? detection.officeIndexBySeat.get(seat) : undefined;
        const office = officeIndex === undefined ? undefined : detection.offices[officeIndex];
        const note = this.diPlacementNotes.get(ch.id);
        return {
          id: ch.id,
          identityKey: note?.identityKey ?? null,
          seatId: seat,
          office: office?.label ?? null,
          seatKind: !seat || !office
            ? null
            : office.workstationIds.includes(seat)
              ? ('workstation' as const)
              : office.tableIds.includes(seat)
                ? ('table, not workstation' as const)
                : ('chair, not workstation' as const),
          placement: note?.placement ?? null,
        };
      })
      .sort((a, b) => a.id - b.id);
    return {
      offices: detection.offices.map((o) => ({
        index: o.index,
        label: o.label,
        kind: o.kind,
        seats: o.seatIds.length,
        workstations: o.workstationIds.length,
        tables: o.tableIds.length,
        otherChairs: o.chairIds.length,
        seatIds: [...o.seatIds],
        workstationIds: [...o.workstationIds],
        tableIds: [...o.tableIds],
      })),
      reservedSeatIds: [...detection.reservedSeatIds],
      seatPreferencesOverBudget: this.seatPreferencesOverBudget,
      characters,
    };
  }

  /** Why saving is withheld for the whole session (a refused migration), or null. Evidence for the UI and tests. */
  get ambientSaveWithheldReason(): string | null {
    return this.ambientSaveWithheld;
  }

  /**
   * BOUNDED HIDDEN-VIEW CATCH-UP: after the view was hidden for `hiddenSeconds`, advance the decorative
   * simulation by at most AMBIENT_CATCHUP_MAX_SEC in fixed steps, minus whatever was already advanced
   * while hidden (`alreadyAdvancedSeconds`, from ambientClock). The game loop's hidden guard means
   * frames do not advance while hidden, so that is normally 0. The rest of the interval is simply not
   * simulated: nothing runs while closed or asleep. Held under reduced motion. Returns the seconds
   * actually advanced.
   */
  catchUpAmbient(hiddenSeconds: number, alreadyAdvancedSeconds = 0): number {
    // only the part of the hidden interval that was NOT already simulated (by any frame that ran while
    // hidden) is caught up, so no interval is ever advanced twice
    const owed = hiddenSeconds - Math.max(0, alreadyAdvancedSeconds);
    if (!this.ambient || this.reducedMotion || !(owed > 0)) return 0;
    const seconds = Math.min(owed, AMBIENT_CATCHUP_MAX_SEC);
    // whole steps, never more than owed (a sub-step remainder is not simulated, so nothing doubles)
    const steps = Math.min(
      Math.floor(seconds / AMBIENT_CATCHUP_STEP_SEC + 1e-6),
      Math.round(AMBIENT_CATCHUP_MAX_SEC / AMBIENT_CATCHUP_STEP_SEC),
    );
    for (let k = 0; k < steps; k++) {
      this.ambient.update(AMBIENT_CATCHUP_STEP_SEC, this.ambientAgentContext());
      this.transit?.update(AMBIENT_CATCHUP_STEP_SEC);
    }
    this.ambientClock += steps * AMBIENT_CATCHUP_STEP_SEC;
    return steps * AMBIENT_CATCHUP_STEP_SEC;
  }

  /** Real agents eligible for the working halo NOW (office/halo.ts; read by the canvas AND the Map). */
  haloCharacterIds(nowSec: number): Set<number> {
    const ids = haloEligibleCharacterIds(this.diOffice, nowSec);
    for (const id of ids) if (!this.characters.has(id)) ids.delete(id);
    return ids;
  }

  /** Take the viewer's city presentation choices (already validated by the caller). */
  setCityPresentation(next: CityPresentation): void {
    this.city.setPresentation(next, Date.now(), this.reducedMotion);
    this.syncTraffic();
  }

  /** The furniture to draw as furniture: the layout's, less the pieces a driving vehicle stands in for. The filtered
   *  list is kept while neither the layout's furniture nor the hidden set has changed (animation frames rebuild the
   *  instances several times a second and must not refilter ten thousand pieces each time). */
  private visibleFurniture(): PlacedFurniture[] {
    const hidden = this.traffic?.hiddenUids();
    if (!hidden || hidden.size === 0) return this.layout.furniture;
    const traffic = this.traffic!;
    const version = traffic.hiddenVersion;
    const cached = this.visibleCache;
    if (cached && cached.traffic === traffic && cached.source === this.layout.furniture && cached.version === version) return cached.list;
    const list = this.layout.furniture.filter((f) => !hidden.has(f.uid));
    this.visibleCache = { traffic, source: this.layout.furniture, version, list };
    return list;
  }
  private visibleCache: { traffic: TrafficWorld; source: PlacedFurniture[]; version: number; list: PlacedFurniture[] } | null = null;

  /** Bring the traffic in line with the choice: built on first need (DI office only), its cap set from the
   *  level, and the furniture instances rebuilt when the set of hidden static copies changed. */
  private syncTraffic(): void {
    const mode = this.city.presentation.traffic;
    const cap = mode === 'normal' ? TRAFFIC_MAX_VEHICLES : mode === 'low' ? TRAFFIC_CAP_LOW : 0;
    if (!this.ambientEnabled || cap === 0) {
      if (this.traffic && this.traffic.activeCap > 0) {
        this.traffic.setCap(0);
        this.rebuildFurnitureInstances();
      }
      return;
    }
    if (!this.traffic) this.traffic = buildTrafficWorld(this.layout, getCatalogEntry, getBlockedTiles);
    const before = this.traffic.hiddenVersion;
    this.traffic.setCap(cap);
    if (this.traffic.hiddenVersion !== before) this.rebuildFurnitureInstances();
  }

  /** Who traffic must give way to: where everyone is and where they are about to step. Reused arrays. */
  private gatherTrafficPeople(): TrafficPeople {
    const p = this.trafficPeople;
    p.agentOwn.length = 0;
    p.agentNear.length = 0;
    p.walkerOwn.length = 0;
    p.walkerNear.length = 0;
    const cols = this.layout.cols;
    const look = TRAFFIC_PEDESTRIAN_LOOKAHEAD_TILES;
    for (const ch of this.getCharacters()) {
      p.agentOwn.push(ch.tileRow * cols + ch.tileCol);
      for (let i = 0; i < ch.path.length && i < look; i++) p.agentNear.push(ch.path[i].row * cols + ch.path[i].col);
    }
    if (this.ambient) {
      // the visible residents only: an off-duty resident is not drawn, so it is no obstacle to a vehicle
      for (const r of this.ambient.visibleResidents()) {
        if (r.state === 'riding') continue;
        p.walkerOwn.push(r.tileRow * cols + r.tileCol);
        if (r.state === 'walk') for (let i = r.routePos; i < r.route.length && i < r.routePos + look; i++) p.walkerNear.push(r.route[i]);
      }
    }
    for (const pet of this.pets) {
      p.walkerOwn.push(pet.tileRow * cols + pet.tileCol);
      for (let i = 0; i < pet.path.length && i < look; i++) p.walkerNear.push(pet.path[i].row * cols + pet.path[i].col);
    }
    return p;
  }

  /** The vehicles to draw (empty with traffic off). */
  getVehicles(): readonly Vehicle[] {
    return this.traffic && this.traffic.activeCap > 0 ? this.traffic.visible() : [];
  }

  /** The traffic itself, for the budget measurements and tests; null until it is first needed. */
  getTraffic(): TrafficWorld | null {
    return this.traffic;
  }

  /** The vehicle under a world-pixel point. Callers check real agents, pets and residents FIRST. */
  getVehicleAt(worldX: number, worldY: number): Vehicle | null {
    return vehicleAt(this.getVehicles(), worldX, worldY);
  }

  /** What the renderer draws for the city this frame, or null when there is nothing to draw (day, no
   *  weather): a day with clear skies costs nothing. */
  cityRenderInput(): CityRenderInput | null {
    const lit = this.city.level >= CITY_LIGHT_VISIBLE_EPSILON;
    const wet = this.city.weather.count > 0;
    if (!lit && !wet) return null;
    const veil = wet ? weatherVeil(this.city.weather.kind) : null;
    const agents: { x: number; y: number }[] = [];
    if (lit || veil) {
      for (const ch of this.getCharacters()) {
        if (ch.matrixEffect === 'despawn') continue;
        agents.push({ x: ch.x, y: ch.y - CITY_LIGHT_AGENT_LIFT_PX });
      }
    }
    return {
      level: lit ? this.city.level : 0,
      mask: cityMaskFor(this.layout),
      lamps: lit ? lampLights(this.layout, footprintOf) : [],
      agents,
      weather: wet ? this.city.weather : null,
      veil,
    };
  }

  /** The trains to draw (empty without a subway or while disabled). */
  getTrains(): readonly Train[] {
    return this.transit?.trains ?? [];
  }

  /** The residents to DRAW (empty while disabled): the ON-DUTY residents. A working office shows at most
   *  min(AMBIENT_WORKING_OFFICE_ON_DUTY, ceil(T / AMBIENT_WORKING_OFFICE_TILES_PER_RESIDENT)) of its residents
   *  at once (T = its pool's resident-eligible tiles); the rest are off duty, still simulated state
   *  (`ambient.residents` is the full roster), and not drawn. */
  getAmbientResidents(): readonly AmbientResident[] {
    return this.ambient?.visibleResidents() ?? [];
  }

  /** The resident under a world-pixel point; never an off-duty one (it is not drawn, so it can never be
   *  hovered or clicked). Callers check real agents FIRST: an agent always wins the pointer over a resident. */
  getAmbientAt(worldX: number, worldY: number): AmbientResident | null {
    return this.ambient?.residentAt(worldX, worldY) ?? null;
  }

  /** Whether a world-pixel point is on the reception desk or its receptionist (always false without
   *  a reception post). Callers check real agents, pets and residents FIRST. */
  getReceptionAt(worldX: number, worldY: number): boolean {
    return this.receptionPost
      ? isReceptionHit(
          this.receptionPost,
          worldX,
          worldY,
          TILE_SIZE,
          CHARACTER_HIT_HEIGHT - TILE_SIZE,
        )
      : false;
  }

  /** The `saveAgentSeats` payload: palette, hue and seat for every agent worth
   *  restoring. Sub-agents are excluded because they are derived state the
   *  runtime re-materializes, and the greeter never reaches here at all —
   *  it is not in `characters`. */
  getPersistableSeats(): Record<
    number,
    { palette: number; hueShift: number; seatId: string | null }
  > {
    const seats: Record<number, { palette: number; hueShift: number; seatId: string | null }> = {};
    for (const ch of this.characters.values()) {
      if (ch.isSubagent) continue;
      seats[ch.id] = { palette: ch.palette, hueShift: ch.hueShift, seatId: ch.seatId };
    }
    return seats;
  }

  /** Everything the renderer draws: the agents plus, while the first-run ask
   *  is up, the consent greeter. This is the ONE place the greeter joins the
   *  agents — every other consumer reads `characters` and gets agents only. */
  getCharacters(): Character[] {
    const chars = Array.from(this.characters.values());
    if (this.greeter) chars.push(this.greeter);
    if (this.receptionist) chars.push(this.receptionist);
    return chars;
  }

  /** Get character at pixel position (for hit testing). Returns id or null.
   *  Agents only: clicks pass straight through the consent greeter, which is
   *  a prop, not something to select or follow. */
  getCharacterAt(worldX: number, worldY: number): number | null {
    const chars = Array.from(this.characters.values()).sort((a, b) => b.y - a.y);
    for (const ch of chars) {
      // Skip characters that are despawning
      if (ch.matrixEffect === 'despawn') continue;
      // Character sprite is 16x24, anchored bottom-center
      // Apply sitting offset to match visual position
      const sittingOffset = ch.state === CharacterState.TYPE ? CHARACTER_SITTING_OFFSET_PX : 0;
      const anchorY = ch.y + sittingOffset;
      const left = ch.x - CHARACTER_HIT_HALF_WIDTH;
      const right = ch.x + CHARACTER_HIT_HALF_WIDTH;
      const top = anchorY - CHARACTER_HIT_HEIGHT;
      const bottom = anchorY;
      if (worldX >= left && worldX <= right && worldY >= top && worldY <= bottom) {
        return ch.id;
      }
    }
    return null;
  }
}
