import { useEffect, useRef, useState } from 'react';

import {
  TILE_SIZE,
  WORLD_MAP_CAMERA_COLOR,
  WORLD_MAP_CAMERA_LINE_CSS_PX,
  WORLD_MAP_HOME_LABEL,
  WORLD_MAP_LABEL_COLOR,
  WORLD_MAP_LABEL_FONT_CSS_PX,
  WORLD_MAP_LABEL_SHADOW_COLOR,
  WORLD_MAP_MARGIN_PX,
} from '../constants.js';
import type { OfficeState } from '../office/engine/officeState.js';
import { renderFrame, renderScene } from '../office/engine/renderer.js';
import { haloIntensity } from '../office/halo.js';
import { districtsOf, fitWorldMap, mapPointToWorld } from '../office/layout/worldViews.js';
import { drawLivingLayer } from '../office/livingLayer.js';
import { startMapTicker } from '../office/mapCadence.js';
import { installMapKeyGuard } from '../office/mapKeyGuard.js';
import { presentationGeneration } from '../office/presentationGeneration.js';
import { mapOffset } from '../office/projection.js';
import { isE2E } from '../runtime.js';
import { canTakeFocus } from './diMinimize.js';
import { CLOSE_FOCUS_FALLBACKS, frontCount } from './diModalStack.js';

interface WorldMapProps {
  officeState: OfficeState;
  /** The main camera, to outline what it currently shows. */
  zoom: number;
  panRef: React.MutableRefObject<{ x: number; y: number }>;
  /** The main canvas backing size (device pixels), or null before it is measured. */
  getCameraCanvas: () => { w: number; h: number } | null;
  /** Called with a world sprite-pixel point the user clicked: the camera goes there. */
  onJump: (worldX: number, worldY: number) => void;
  onClose: () => void;
}

/**
 * THE MAP — the whole world at once, PRESENTATION ONLY (no message, nothing saved). The world's
 * STATIC layers (tiles + colours, carpets, area wash, walls and furniture) are rendered ONCE at
 * 1 sprite pixel per pixel into an offscreen cache; each tick of the (inherited, unchanged) slow
 * timer then only blits that cache at display size and draws the LIVE layer over it — every
 * worker and pet at its current position — and the main camera's outline. So the per-tick cost is
 * bounded by the display and the number of characters, not by the world's cells (a 352 x 224
 * world made the old per-tick full re-render a 60-70 ms long task). The cache is rebuilt when
 * anything it shows changes: the layout object, its tiles / tileColors / carpetTiles / areas /
 * areaTiles arrays, the furniture instances (placed, moved, recoloured, auto-state swaps), the
 * tile map, or a sprite / catalogue (re)load (presentationGeneration). Furniture ANIMATION frames
 * are held at the frame of the last rebuild on the Map (the main view animates as before).
 * Every world edge is visible (fitWorldMap); Esc or the Map button closes it; a click centres the
 * camera on that spot.
 */
/**
 * K1 round 17: where focus goes when the Map's OWN Escape closes it. Focus on a control that keeps it (the toggle, another world control) stays there.
 * Focus fallen to the page body (the path the coverage term guards: nothing covers the Map) goes to the Map's OPENER, its toggle; when the world's
 * chrome is collapsed and the toggle is not drawn, to the world's disclosure, which brings it back; then to the shared close-time fallbacks
 * (diModalStack.ts). It is never left on the body.
 */
const MAP_CLOSE_FOCUS = ['[data-testid="world-view-map"]', '[data-testid="di-world-disclosure"]', ...CLOSE_FOCUS_FALLBACKS] as const;
function closeByKeyboard(onClose: () => void, host: HTMLElement | null): void {
  const a = document.activeElement;
  const keeps = a instanceof HTMLElement && a !== document.body && host?.contains(a) !== true && canTakeFocus(a);
  onClose();
  if (keeps) return;
  for (const selector of MAP_CLOSE_FOCUS) {
    const target = document.querySelector<HTMLElement>(selector);
    if (!canTakeFocus(target)) continue;
    target.focus();
    if (document.activeElement === target) return;
  }
}

/**
 * K1 (C): a surface that covers the world (`data-di-covers-world`: the scan stage, the request workspace while it covers) is RENDERED now and its box
 * CONTAINS the Map's. Read at the key from what is drawn, never from an attribute that can outlive what it describes (a minimized panel keeps its
 * stage mounted but hidden: `checkVisibility` is false, so it covers nothing).
 */
function mapCoveredNow(host: HTMLElement | null): boolean {
  if (host === null) return false;
  const m = host.getBoundingClientRect();
  for (const el of document.querySelectorAll<HTMLElement>('[data-di-covers-world]')) {
    if (!el.checkVisibility({ checkVisibilityCSS: true })) continue;
    const r = el.getBoundingClientRect();
    if (r.left <= m.left + 1 && r.top <= m.top + 1 && r.right >= m.right - 1 && r.bottom >= m.bottom - 1) return true;
  }
  return false;
}

/**
 * K1 (F): focus is on a VISIBLE element outside the Map's own region. The Map's region is the Map itself and the world's own controls
 * (`[data-di-world-controls]`, the labelled Map toggle and the disclosure among them): focus there leaves the keys with the Map, so Escape on the
 * toggle closes the Map. A focused element that is not visible (inside a minimized panel) owns nothing.
 */
function focusOwnedOutsideMap(host: HTMLElement | null): boolean {
  const a = document.activeElement;
  if (!(a instanceof HTMLElement) || a === document.body) return false;
  if (!a.checkVisibility({ checkVisibilityCSS: true })) return false;
  if (host?.contains(a) === true) return false;
  return a.closest('[data-di-world-controls]') === null;
}

export function WorldMap({
  officeState,
  zoom,
  panRef,
  getCameraCanvas,
  onJump,
  onClose,
}: WorldMapProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [host, setHost] = useState({ w: 0, h: 0 });
  const layout = officeState.getLayout();
  const worldW = layout.cols * TILE_SIZE;
  const worldH = layout.rows * TILE_SIZE;
  const rect = fitWorldMap(worldW, worldH, host.w, host.h, WORLD_MAP_MARGIN_PX);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const measure = () => setHost({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // While the Map is open it owns the keyboard (installMapKeyGuard): nothing reaches the editor.
  // Two things in front of it own the keyboard instead, and the guard yields to either:
  //  - a modal dialog opened over it (diModal.ts makes #root inert while one is open);
  //  - a non-modal popover over the world (the City panel): the world's controls, this Map's own gate among them, are SUSPENDED while it is open
  //    (WorldControlsGate, data-di-world-controls="suspended"). `inert` affects elements and never a WINDOW listener, so this listener must ask for
  //    itself, at the moment of each key, "is something foreground over me"; otherwise Escape pressed in the popover would close the Map (destroying
  //    its open state) and leave the popover open. The Map stays open and gets the keyboard back when the popover closes.
  useEffect(
    () =>
      installMapKeyGuard(
        window,
        // round 17: the keyboard close gives focus an intentional, visible destination (closeByKeyboard)
        () => closeByKeyboard(onClose, hostRef.current),
        () =>
          document.getElementById('root')?.hasAttribute('inert') === true ||
          // B1: a NON-MODAL popover in front sets no inert, but it owns Escape: the Map yields to any claim in front (diModalStack)
          frontCount() > 0 ||
          hostRef.current?.closest('[data-di-world-controls="suspended"]') != null ||
          // K1 (the Supervisor's ruling, .herd/state/k1-keyboard-ownership-ruling.md): the Map owns the keys only while it is the surface the person is
          // using. A visible surface holding focus owns its keys (F), and a surface that COVERS the Map now owns them when focus has fallen to the body
          // (C). Both are read AT THE KEY; hidden or minimized content owns nothing. Isolation is kept by behaviour, not by stopping every key: the
          // world-pan stands down while the Map is drawn (App.tsx), and the editor's keys need edit mode, which closes the Map.
          mapCoveredNow(hostRef.current) ||
          focusOwnedOutsideMap(hostRef.current),
      ),
    [onClose],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const cache = document.createElement('canvas');
    /** The static layers scaled to the display size: what each tick blits 1:1. */
    const scaled = document.createElement('canvas');
    let cacheKey: unknown[] = [];
    let scaledKey = '';
    /** Per-tick cost, attributable by part: (a) static re-render, (b) downscale of the static
     *  layers to the display size, (c) the per-tick blit + live layer + outline + labels. */
    const stats = {
      staticRebuilds: 0,
      staticMsTotal: 0,
      staticMsMax: 0,
      scaledRebuilds: 0,
      scaledMsTotal: 0,
      scaledMsMax: 0,
      tickMsTotal: 0,
      tickMsMax: 0,
    };
    /** READABLE LABELS: every named district (layout.areas, as data) and the home, at their
     *  boxes' centres in world px — recomputed with the static cache. */
    let labels: Array<{ text: string; x: number; y: number }> = [];
    /** Re-render the static layers into the cache only when one of their inputs changed. The
     *  furniture key is officeState.furnitureStaticVersion, NOT the instance array: animation
     *  frames rebuild that array several times a second and must not re-render the world. */
    const ensureStatic = () => {
      const l = officeState.getLayout();
      const key = [
        l,
        l.tiles,
        l.tileColors,
        l.carpetTiles,
        l.areas,
        l.areaTiles,
        officeState.furnitureStaticVersion,
        officeState.tileMap,
        presentationGeneration(),
      ];
      if (key.length === cacheKey.length && key.every((v, i) => v === cacheKey[i])) return false;
      cacheKey = key;
      stats.staticRebuilds++;
      labels = districtsOf(l).map((d) => ({
        text: d.label,
        x: ((d.box.c0 + d.box.c1) / 2) * TILE_SIZE,
        y: ((d.box.r0 + d.box.r1) / 2) * TILE_SIZE,
      }));
      if (l.home)
        labels.push({
          text: WORLD_MAP_HOME_LABEL,
          x: ((l.home.c0 + l.home.c1) / 2) * TILE_SIZE,
          y: l.home.r0 * TILE_SIZE - TILE_SIZE,
        });
      const w = l.cols * TILE_SIZE;
      const h = l.rows * TILE_SIZE;
      cache.width = w;
      cache.height = h;
      const cctx = cache.getContext('2d');
      if (!cctx) return true;
      cctx.imageSmoothingEnabled = false;
      renderFrame(
        cctx,
        w,
        h,
        officeState.tileMap,
        officeState.furniture,
        [],
        1,
        0,
        0,
        undefined,
        undefined,
        l.tileColors,
        l.cols,
        l.rows,
        l.carpetTiles,
        l.areas,
        l.areaTiles,
        false,
        null,
        [],
      );
      return true;
    };
    // what the living layer drew on the last tick (evidence hook below)
    let livingCounts = { residents: 0, trains: 0, vehicles: 0 };
    const draw = () => {
      const t0 = performance.now();
      const rebuilt = ensureStatic();
      const t1 = performance.now();
      if (rebuilt) {
        stats.staticMsTotal += t1 - t0;
        stats.staticMsMax = Math.max(stats.staticMsMax, t1 - t0);
      }
      const l = officeState.getLayout();
      const dpr = window.devicePixelRatio || 1;
      const cw = Math.max(1, Math.round(rect.w * dpr));
      const ch = Math.max(1, Math.round(rect.h * dpr));
      if (canvas.width !== cw) canvas.width = cw;
      if (canvas.height !== ch) canvas.height = ch;
      // the scaled static layer: rebuilt only with the cache or a new display size
      const sk = `${cw}x${ch}`;
      let t2 = performance.now();
      if (rebuilt || sk !== scaledKey) {
        scaledKey = sk;
        stats.scaledRebuilds++;
        scaled.width = cw;
        scaled.height = ch;
        const sctx = scaled.getContext('2d');
        if (sctx) {
          sctx.imageSmoothingEnabled = true;
          sctx.drawImage(cache, 0, 0, cw, ch);
        }
        const t = performance.now();
        stats.scaledMsTotal += t - t2;
        stats.scaledMsMax = Math.max(stats.scaledMsMax, t - t2);
        t2 = t;
      }
      const sx = cw / (l.cols * TILE_SIZE);
      const sy = ch / (l.rows * TILE_SIZE);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.drawImage(scaled, 0, 0);
      // the LIVING layer (C3): ambient residents, trains and decorative traffic as markers, from the same
      // live objects the canvas draws, at this Map's cadence; drawn FIRST so real agents and pets sit on top
      livingCounts = drawLivingLayer(
        ctx,
        officeState.getAmbientResidents(),
        officeState.getTrains(),
        sx,
        sy,
        officeState.getVehicles(),
      );
      // the LIVE layer: every worker and pet where it is now (1 sprite px = sx canvas px)
      ctx.setTransform(sx, 0, 0, sy, 0, 0);
      ctx.imageSmoothingEnabled = false;
      // the halo from the SAME function the canvas uses (office/halo.ts), steady at Map cadence
      renderScene(ctx, [], officeState.getCharacters(), 0, 0, 1, null, null, officeState.pets, [], [], {
        ids: officeState.haloCharacterIds(Date.now() / 1000),
        intensity: haloIntensity(0, true),
      });
      // The main camera's view, in world sprite pixels.
      const main = getCameraCanvas();
      if (main && rect.scale > 0) {
        const { offsetX, offsetY } = mapOffset(
          main.w,
          main.h,
          l.cols,
          l.rows,
          zoom,
          panRef.current.x,
          panRef.current.y,
        );
        ctx.strokeStyle = WORLD_MAP_CAMERA_COLOR;
        ctx.lineWidth = (WORLD_MAP_CAMERA_LINE_CSS_PX * dpr) / Math.max(sx, 1e-6);
        ctx.strokeRect(-offsetX / zoom, -offsetY / zoom, main.w / zoom, main.h / zoom);
      }
      // labels on top, at display resolution (crisp at any Map scale)
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      const px = Math.round(WORLD_MAP_LABEL_FONT_CSS_PX * dpr);
      ctx.font = `bold ${px}px 'FS Pixel Sans'`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      for (const lb of labels) {
        const x = lb.x * sx;
        const y = lb.y * sy;
        ctx.lineWidth = Math.max(2, Math.round(px / 4));
        ctx.strokeStyle = WORLD_MAP_LABEL_SHADOW_COLOR;
        ctx.strokeText(lb.text, x, y);
        ctx.fillStyle = WORLD_MAP_LABEL_COLOR;
        ctx.fillText(lb.text, x, y);
      }
      const tick = performance.now() - t2;
      stats.tickMsTotal += tick;
      stats.tickMsMax = Math.max(stats.tickMsMax, tick);
    };
    // ONE timer per mount (the inherited, unchanged cadence), cleared on unmount
    let draws = 0;
    const tick = () => {
      draw();
      draws++;
    };
    if (isE2E) {
      // e2e/evidence only: read-only observables of the Map's work
      const hooks = ((
        window as unknown as Record<string, Record<string, unknown>>
      ).__pixelAgentsTestHooks ??= {});
      hooks.worldMapStatic = () => (ensureStatic(), cache);
      hooks.worldMapStats = () => ({ draws, ...stats, living: livingCounts });
      // a getter (redefined on every mount; never assigned to)
      Object.defineProperty(hooks, 'worldMapDraws', { get: () => draws, configurable: true });
    }
    return startMapTicker(tick, officeState.reducedMotion);
  }, [officeState, zoom, panRef, getCameraCanvas, rect.scale, rect.w, rect.h]);

  return (
    <div
      ref={hostRef}
      className="absolute inset-0 z-20"
      style={{ background: 'var(--pixel-bg)' }}
      data-testid="world-map"
      onClick={onClose}
    >
      <canvas
        ref={canvasRef}
        data-testid="world-map-canvas"
        title="The whole world. Click a spot to go there. Dots are decorative residents, bars are trains, boxes are decorative traffic."
        className="absolute pixel-panel"
        style={{
          left: rect.x,
          top: rect.y,
          width: rect.w,
          height: rect.h,
          cursor: 'crosshair',
        }}
        onClick={(e) => {
          e.stopPropagation();
          const box = e.currentTarget.getBoundingClientRect();
          const hostBox = hostRef.current?.getBoundingClientRect();
          const p = mapPointToWorld(
            rect,
            e.clientX - (hostBox?.left ?? box.left - rect.x),
            e.clientY - (hostBox?.top ?? box.top - rect.y),
            worldW,
            worldH,
          );
          onJump(p.x, p.y);
        }}
      />
    </div>
  );
}
