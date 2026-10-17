import type { ColorValue } from './components/ui/types.js';
import {
  eraseCarpet,
  expandLayout,
  paintCarpet,
  paintTile,
} from './office/editor/editorActions.js';
import { OfficeState } from './office/engine/officeState.js';
import {
  carpetLayerBlits,
  carpetLayerBuilds,
  isGhostHeadlessAgentsEnabled,
  renderCarpetLayer,
  renderFrame,
  setCarpetLayerCacheEnabled,
} from './office/engine/renderer.js';
import { visibleTiles } from './office/projection.js';
import { renderCacheStats, resetRenderCaches } from './office/renderCaches.js';
import {
  carpetJunctionCase,
  getCarpetSetCount,
  getCarpetVariantSprites,
  setCarpetSprites,
} from './office/sprites/carpetTiles.js';
import { TileType } from './office/types.js';

declare global {
  interface Window {
    __pixelAgentsTestHooks?: {
      playedSounds?: Array<{ kind: string; at: number }>;
      getCharacters?: () => Array<{
        id: number;
        /** World-pixel anchor (bottom-centre) and FSM state — read-only, for aiming real clicks. */
        x: number;
        y: number;
        state: string;
        matrixEffect: 'spawn' | 'despawn' | null;
        agentName?: string;
        bubbleType: 'permission' | 'waiting' | null;
        waitingAwaitingInput?: boolean;
        isHeadless?: boolean;
        isGreeter?: boolean;
      }>;
      /** Effective "Display headless as ghosts" setting the renderer is using. */
      getGhostHeadlessAgents?: () => boolean;
      /** Render ONLY the carpet layer into a fresh w x h canvas at (offsetX, offsetY, zoom), with
       *  the pre-composed layer on or off; returns a hash of its pixels and how many layers were
       *  built so far — the equivalence probe for the pre-composed carpet layer. */
      renderCarpetProbe?: (
        cached: boolean,
        w: number,
        h: number,
        offsetX: number,
        offsetY: number,
        zoom: number,
        /** first apply a real layout change: the EDITOR's own paint / erase / grid expansion
         *  (editorActions), or a carpet sprite-set RELOAD (setCarpetSprites, same sprites) */
        edit?: { kind: 'paint' | 'erase' | 'expand' | 'reload'; col: number; row: number },
      ) => {
        hash: string;
        opaque: number;
        layerBuilds: number;
        layerBlits: number;
        renderMs: number;
        /** false when the edit could not apply (e.g. an expansion refused at the cell cap) */
        editApplied: boolean;
      };
      /** An EDIT cycle without the editor UI: paint n floor cells (from (col0, row0), row-major,
       *  `width` wide) each with a NEW colour from `seed`, via editorActions.paintTile, then
       *  OfficeState.rebuildFromLayout — the two calls the editor's applyEdit makes (it also
       *  saves; this does not). */
      paletteEditProbe?: (
        seed: number,
        n: number,
        col0: number,
        row0: number,
        width: number,
      ) => { painted: number };
      /** Render the world rectangle [c0, c1) x [r0, r1) through the real renderFrame into an
       *  offscreen canvas at `zoom` and return it as a PNG data URL — the renderer's own output
       *  with NO UI overlay (the art-QA capture path for sprites the on-screen UI would cover). */
      renderRegionProbe?: (c0: number, r0: number, c1: number, r1: number, zoom: number) => string;
      /** Render-cache accounting (office/renderCaches.ts). */
      renderCacheStats?: () => {
        colorizeEntries: number;
        carpetEntries: number;
        carpetLayerPixels: number;
        resets: number;
      };
      /** Render the WHOLE current scene (floors, walls, carpets, furniture, characters, pets) at
       *  the given view into a fresh canvas, TWICE in one task — before and after a full render-
       *  cache reset — and hash both: the scene must re-render identically right after a clear. */
      renderAfterResetProbe?: (
        w: number,
        h: number,
        zoom: number,
        panX: number,
        panY: number,
      ) => {
        before: string;
        after: string;
        opaque: number;
        statsAfterReset: ReturnType<typeof renderCacheStats>;
        statsAfterRefresh: ReturnType<typeof renderCacheStats>;
      };
      // ── Carpet + Areas observability (added for carpet/areas e2e) ──
      /** Sparse list of painted carpet tiles with their grid coords. */
      getCarpetTiles?: () => Array<{
        col: number;
        row: number;
        variant: number;
        color?: ColorValue;
        accentColor?: ColorValue;
        order?: number;
      }>;
      /** 4-bit marching-squares case for a junction (NW=1,NE=2,SE=4,SW=8) via the
       *  real renderer logic — for asserting carpet autotiling. */
      getCarpetJunctionCase?: (jx: number, jy: number, variant: number) => number;
      /** Area definitions in the current layout. */
      getAreas?: () => Array<{ label: string; color: string }>;
      /** Sparse list of area-painted tiles with their grid coords. */
      getAreaTiles?: () => Array<{ col: number; row: number; label: string }>;
      /** Folder→Area mappings received by OfficeState. */
      getAreaMappings?: () => Record<string, string[]>;
      /** Effective show-areas gate (settings toggle OR active area edit). */
      getShowAreas?: () => boolean;
      /** The Map's point jump: centre world point (x, y) at the office view's legible zoom. */
      jumpToWorldPoint?: (x: number, y: number) => void;
      /** The view the renderer uses: integer zoom (device px per sprite px), pan, grid size. */
      getView?: () => { zoom: number; panX: number; panY: number; cols: number; rows: number };
      /** Count of placed furniture instances — lets a spec assert furniture
       *  placed onto a carpet tile (surface placement) without it being blocked. */
      getFurnitureCount?: () => number;
      /** Seated top-level agents with the area their seat falls in (or null). */
      getAgentSeats?: () => Array<{
        id: number;
        seatId: string | null;
        areaLabel: string | null;
        folderName?: string;
      }>;
      /** All seats with grid coords + the area their tile falls in — lets a spec
       *  paint an Area over a known seat without hardcoding layout coordinates. */
      getSeats?: () => Array<{
        uid: string;
        col: number;
        row: number;
        areaLabel: string | null;
        assigned: boolean;
      }>;
      /** Drive the real edit-mode tile paint/erase handlers by (col,row),
       *  bypassing only canvas pixel→tile geometry (mirrors petClick). */
      editorTileAction?: (col: number, row: number) => void;
      /** DI only: the RECORDED canonical identity per drawn characterId (the animation map). */
      getDiRecorded?: () => Array<[number, string]>;
      /** DI only: the last office state the handler RECEIVED (what the layout-ready flush uses). */
      getDiStored?: () => unknown;
      editorEraseAction?: (col: number, row: number) => void;
      getPets?: () => Array<{
        id: string;
        name: string;
        petType: number;
        state: 'idle' | 'walk' | 'follow';
        x: number;
        y: number;
        bubbleType: 'heart' | null;
      }>;
      petClick?: (petId: string) => void;
      addAgentLog?: Array<{
        id: number;
        skipSpawnEffect: boolean | undefined;
        matrixEffectAtCreation: 'spawn' | 'despawn' | null;
      }>;
      messageLog?: Array<{
        at: number;
        type: string;
        id?: number;
        toolName?: string;
        status?: string;
        toolId?: string;
        parentToolId?: string;
      }>;
      selectAgent?: (id: number) => void;
    };
  }
}

/**
 * Install e2e test observables on window.__pixelAgentsTestHooks. Mostly
 * read-only / append-only; the one action (selectAgent) only sets selection
 * state and changes no production logic. Called once at module-load from
 * App.tsx with the singleton officeStateRef.
 *
 * - getCharacters(): point-in-time snapshot of every character's matrix, team, and bubble state.
 * - addAgentLog: append-only history of every OfficeState.addAgent call. The
 *   log captures matrixEffect AT addAgent time (synchronously inside the
 *   wrapper), eliminating the ~300ms matrix-effect lifetime race that would
 *   let a regression slip past a snapshot-based check.
 * - playedSounds: populated separately by notificationSound.ts (same namespace,
 *   different owner).
 * - selectAgent(id): sets officeState.selectedAgentId directly, the same state
 *   a canvas click produces. Lets e2e reveal an agent's "Close agent" (×)
 *   button deterministically instead of pixel-hunting the sprite on the canvas
 *   (see closeAgentFromOverlay in e2e/helpers/office.ts). ToolOverlay reads
 *   selectedAgentId every rAF, so the × button surfaces on the next frame.
 */
/** FNV-1a over every pixel byte, and the count of non-transparent pixels. */
function pixelHash(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
): { hash: string; opaque: number } {
  const px = ctx.getImageData(0, 0, w, h).data;
  let hsh = 0x811c9dc5;
  let opaque = 0;
  for (let i = 0; i < px.length; i++) {
    hsh = Math.imul(hsh ^ px[i], 0x01000193) >>> 0;
    if (i % 4 === 3 && px[i] !== 0) opaque++;
  }
  return { hash: hsh.toString(16), opaque };
}

export function installTestHooks(officeStateRef: { current: OfficeState | null }): void {
  if (typeof window === 'undefined') return;
  if (!window.__pixelAgentsTestHooks) window.__pixelAgentsTestHooks = {};
  const hooks = window.__pixelAgentsTestHooks;
  if (!hooks.addAgentLog) hooks.addAgentLog = [];

  hooks.getCharacters = () => {
    const os = officeStateRef.current;
    if (!os) return [];
    // getCharacters(), not `characters`: e2e asserts on the consent greeter,
    // which lives outside the agent map and joins only at the render seam.
    return os.getCharacters().map((ch) => ({
      id: ch.id,
      x: ch.x,
      y: ch.y,
      state: ch.state,
      matrixEffect: ch.matrixEffect,
      agentName: ch.agentName,
      bubbleType: ch.bubbleType,
      waitingAwaitingInput: ch.waitingAwaitingInput,
      isHeadless: ch.isHeadless,
      isGreeter: ch.isGreeter,
    }));
  };

  // The ghost setting lives in the renderer module (read every rAF frame), not
  // in OfficeState, so e2e reads it from there to assert what is actually drawn.
  hooks.getGhostHeadlessAgents = () => isGhostHeadlessAgentsEnabled();

  hooks.selectAgent = (id) => {
    const os = officeStateRef.current;
    if (os) os.selectedAgentId = id;
  };

  // Point-in-time snapshot of every live pet. Pets render only on the canvas
  // (no DOM) and the heart bubble is never persisted, so e2e reads pet state
  // through here — the same rationale as getCharacters() above.
  hooks.getPets = () => {
    const os = officeStateRef.current;
    if (!os) return [];
    return os.pets.map((pet) => ({
      id: pet.id,
      name: pet.name,
      petType: pet.petType,
      state: pet.state,
      x: pet.x,
      y: pet.y,
      bubbleType: pet.bubbleType,
    }));
  };

  // Drive the same state a canvas click on a pet produces (toggle the heart
  // bubble). Mirrors OfficeCanvas's pet-hit branch but takes a known petId
  // instead of a hit-test result, so tests don't pixel-hunt the randomly
  // spawned sprite — the same tradeoff selectAgent makes for characters.
  hooks.petClick = (petId) => {
    const os = officeStateRef.current;
    if (!os) return;
    const pet = os.pets.find((p) => p.id === petId);
    if (!pet) return;
    if (pet.bubbleType) {
      os.dismissPetBubble(petId);
    } else {
      os.showPetBubble(petId);
    }
  };

  hooks.renderCacheStats = () => renderCacheStats();
  hooks.renderRegionProbe = (c0, r0, c1, r1, zoom) => {
    const os = officeStateRef.current;
    const s = 16 * zoom;
    const w = (c1 - c0) * s;
    const h = (r1 - r0) * s;
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d')!;
    ctx.imageSmoothingEnabled = false;
    if (os) {
      const layout = os.getLayout();
      // renderFrame centres the map then adds the pan: choose the pan that puts (c0, r0) at (0, 0)
      const panX = -c0 * s - Math.floor((w - layout.cols * s) / 2);
      const panY = -r0 * s - Math.floor((h - layout.rows * s) / 2);
      renderFrame(
        ctx,
        w,
        h,
        os.tileMap,
        os.furniture,
        os.getCharacters(),
        zoom,
        panX,
        panY,
        undefined,
        undefined,
        layout.tileColors,
        layout.cols,
        layout.rows,
        layout.carpetTiles,
        layout.areas,
        layout.areaTiles,
        false,
        null,
        os.pets,
      );
    }
    return canvas.toDataURL('image/png');
  };
  hooks.paletteEditProbe = (seed, n, col0, row0, width) => {
    const os = officeStateRef.current;
    if (!os) return { painted: 0 };
    let layout = os.getLayout();
    let painted = 0;
    for (let i = 0; i < n; i++) {
      const col = col0 + (i % width);
      const row = row0 + Math.floor(i / width);
      const tile = layout.tiles[row * layout.cols + col];
      if (tile === undefined || tile === TileType.WALL || tile === TileType.VOID) continue;
      const k = seed * n + i;
      layout = paintTile(layout, col, row, tile, {
        h: (k % 361) - 180,
        s: (Math.floor(k / 361) % 201) - 100,
        b: (Math.floor(k / 72_561) % 201) - 100,
        c: 0,
        colorize: true,
      });
      painted++;
    }
    os.rebuildFromLayout(layout);
    return { painted };
  };
  hooks.renderAfterResetProbe = (w, h, zoom, panX, panY) => {
    const os = officeStateRef.current;
    const draw = () => {
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d')!;
      ctx.imageSmoothingEnabled = false;
      if (os) {
        const layout = os.getLayout();
        renderFrame(
          ctx,
          w,
          h,
          os.tileMap,
          os.furniture,
          os.getCharacters(),
          zoom,
          panX,
          panY,
          undefined,
          undefined,
          layout.tileColors,
          layout.cols,
          layout.rows,
          layout.carpetTiles,
          layout.areas,
          layout.areaTiles,
          false,
          null,
          os.pets,
        );
      }
      return pixelHash(ctx, w, h);
    };
    const before = draw();
    resetRenderCaches();
    const statsAfterReset = renderCacheStats(); // nothing kept
    // the furniture instances hold colorized sprites: rebuild them as a layout rebuild would
    os?.refreshFurnitureSprites();
    const statsAfterRefresh = renderCacheStats(); // the furniture variants, recomputed
    const after = draw();
    return {
      before: before.hash,
      after: after.hash,
      opaque: after.opaque,
      statsAfterReset,
      statsAfterRefresh,
    };
  };
  hooks.renderCarpetProbe = (cached, w, h, offsetX, offsetY, zoom, edit) => {
    const current = officeStateRef.current?.getLayout();
    let layout = current;
    if (current && edit?.kind === 'paint') layout = paintCarpet(current, edit.col, edit.row, 0);
    if (current && edit?.kind === 'erase') layout = eraseCarpet(current, edit.col, edit.row);
    if (current && edit?.kind === 'expand')
      layout = expandLayout(current, 'left')?.layout ?? current;
    const editApplied = edit ? edit.kind === 'reload' || layout !== current : false;
    if (edit?.kind === 'reload')
      setCarpetSprites(
        Array.from({ length: getCarpetSetCount() }, (_, v) => getCarpetVariantSprites(v) ?? []),
      );
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d')!;
    ctx.imageSmoothingEnabled = false;
    let renderMs = 0;
    if (layout?.carpetTiles) {
      setCarpetLayerCacheEnabled(cached);
      const view = visibleTiles(w, h, offsetX, offsetY, zoom, layout.cols, layout.rows);
      const t0 = performance.now();
      renderCarpetLayer(
        ctx,
        layout.carpetTiles,
        layout.cols,
        layout.rows,
        offsetX,
        offsetY,
        zoom,
        view,
      );
      renderMs = performance.now() - t0;
      setCarpetLayerCacheEnabled(true);
    }
    return {
      ...pixelHash(ctx, w, h),
      layerBuilds: carpetLayerBuilds(),
      layerBlits: carpetLayerBlits(),
      renderMs,
      editApplied,
    };
  };

  // ── Carpet + Areas read hooks (canvas-only state, read like getPets) ──
  hooks.getCarpetTiles = () => {
    const os = officeStateRef.current;
    if (!os) return [];
    const layout = os.getLayout();
    const tiles = layout.carpetTiles;
    if (!tiles) return [];
    const out: Array<{
      col: number;
      row: number;
      variant: number;
      color?: ColorValue;
      accentColor?: ColorValue;
      order?: number;
    }> = [];
    for (let i = 0; i < tiles.length; i++) {
      const t = tiles[i];
      if (!t) continue;
      out.push({
        col: i % layout.cols,
        row: Math.floor(i / layout.cols),
        variant: t.variant,
        color: t.color,
        accentColor: t.accentColor,
        order: t.order,
      });
    }
    return out;
  };

  hooks.getCarpetJunctionCase = (jx, jy, variant) => {
    const os = officeStateRef.current;
    if (!os) return 0;
    const layout = os.getLayout();
    return carpetJunctionCase(jx, jy, variant, layout.carpetTiles ?? [], layout.cols, layout.rows);
  };

  hooks.getAreas = () => {
    const os = officeStateRef.current;
    if (!os) return [];
    return (os.getLayout().areas ?? []).map((a) => ({ label: a.label, color: a.color }));
  };

  hooks.getAreaTiles = () => {
    const os = officeStateRef.current;
    if (!os) return [];
    const layout = os.getLayout();
    const tiles = layout.areaTiles;
    if (!tiles) return [];
    const out: Array<{ col: number; row: number; label: string }> = [];
    for (let i = 0; i < tiles.length; i++) {
      const label = tiles[i];
      if (!label) continue;
      out.push({ col: i % layout.cols, row: Math.floor(i / layout.cols), label });
    }
    return out;
  };

  hooks.getAreaMappings = () => {
    const os = officeStateRef.current;
    if (!os) return {};
    return os.areaMappings;
  };

  hooks.getFurnitureCount = () => {
    const os = officeStateRef.current;
    if (!os) return 0;
    return os.furniture.length;
  };

  hooks.getAgentSeats = () => {
    const os = officeStateRef.current;
    if (!os) return [];
    return Array.from(os.characters.values())
      .filter((ch) => !ch.isSubagent)
      .map((ch) => ({
        id: ch.id,
        seatId: ch.seatId,
        areaLabel: ch.seatId ? os.seatZone(ch.seatId) : null,
        folderName: ch.folderName,
      }));
  };

  hooks.getSeats = () => {
    const os = officeStateRef.current;
    if (!os) return [];
    return Array.from(os.seats.entries()).map(([uid, seat]) => ({
      uid,
      col: seat.seatCol,
      row: seat.seatRow,
      areaLabel: os.seatZone(uid),
      assigned: seat.assigned,
    }));
  };

  const origAddAgent = OfficeState.prototype.addAgent;
  OfficeState.prototype.addAgent = function (
    id,
    preferredPalette,
    preferredHueShift,
    preferredSeatId,
    skipSpawnEffect,
    folderName,
    nearAgentId,
  ) {
    origAddAgent.call(
      this,
      id,
      preferredPalette,
      preferredHueShift,
      preferredSeatId,
      skipSpawnEffect,
      folderName,
      nearAgentId,
    );
    const ch = this.characters.get(id);
    hooks.addAgentLog?.push({
      id,
      skipSpawnEffect,
      matrixEffectAtCreation: ch?.matrixEffect ?? null,
    });
  };
}
