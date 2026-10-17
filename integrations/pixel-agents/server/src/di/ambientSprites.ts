/**
 * DI office: the AMBIENT CHARACTER SHEETS (M3 S6) — the prepared job and Easter-egg sheets under
 * dist/assets/ambient/<id>/ (manifest.json + <id>.png, written by scripts/prepare-living-world-art.ts),
 * decoded into frames for the `ambientSpritesLoaded` handshake message.
 *
 * BOUNDS, in the order they run (a sheet failing any is skipped whole, logged, never partly sent):
 * 1. at most AMBIENT_SPRITE_SHEETS_MAX sheets;
 * 2. the manifest's file size, then its fields: four facing rows named DOWN/LEFT/RIGHT/UP, at most
 *    AMBIENT_SPRITE_COLUMNS_MAX columns, frames at most AMBIENT_SPRITE_FRAME_MAX_PX on a side, every
 *    walk/idle/gesture index inside the columns;
 * 3. the PNG's file size (this bounds what is READ, not what decoding would allocate);
 * 4. the PNG's DECLARED dimensions, read from its IHDR header (24 bytes, no decoding), must equal
 *    columns x frameWidth by 4 x frameHeight — so the decoded allocation is bounded by the manifest
 *    bounds above BEFORE PNG.sync.read is ever called;
 * 5. only then decode (and re-check the decoded dimensions).
 * Decorative art only.
 */
import * as fs from 'fs';
import * as path from 'path';
import { PNG } from 'pngjs';

import { rgbaToHex } from '../../../core/src/assets/colorUtils.js';
import type { AmbientSpriteSheet } from '../../../core/src/messages.js';
import {
  AMBIENT_SPRITE_COLUMNS_MAX,
  AMBIENT_SPRITE_DIR,
  AMBIENT_SPRITE_FRAME_MAX_PX,
  AMBIENT_SPRITE_MANIFEST_MAX_BYTES,
  AMBIENT_SPRITE_PNG_MAX_BYTES,
  AMBIENT_SPRITE_SHEETS_MAX,
} from '../constants.js';

const FACINGS = ['DOWN', 'LEFT', 'RIGHT', 'UP'];
const ID = /^[a-z0-9][a-z0-9-]{0,63}$/;

/** Why a sheet folder is not admissible, or null. */
function checkManifest(m: Record<string, unknown>, folder: string): string | null {
  if (m.id !== folder || typeof m.id !== 'string' || !ID.test(m.id)) return 'id must equal its folder name';
  if (typeof m.label !== 'string' || m.label.length === 0 || m.label.length > 64) return 'label malformed';
  const fw = m.frameWidth;
  const fh = m.frameHeight;
  if (!Number.isInteger(fw) || !Number.isInteger(fh) || (fw as number) < 1 || (fh as number) < 1) return 'frame size malformed';
  if ((fw as number) > AMBIENT_SPRITE_FRAME_MAX_PX || (fh as number) > AMBIENT_SPRITE_FRAME_MAX_PX) return 'frame over the size bound';
  if (!Array.isArray(m.rows) || m.rows.length !== 4 || !FACINGS.every((f) => (m.rows as unknown[]).includes(f))) return 'rows must name DOWN, LEFT, RIGHT and UP';
  if (!Array.isArray(m.columns) || m.columns.length < 1 || m.columns.length > AMBIENT_SPRITE_COLUMNS_MAX) return 'columns malformed or over the bound';
  const cols = m.columns.length;
  for (const k of ['walk', 'idle', 'gesture']) {
    const v = m[k];
    if (!Array.isArray(v) || v.length < 1 || !v.every((i) => Number.isInteger(i) && i >= 0 && i < cols)) return `${k} indices malformed`;
  }
  return null;
}

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

/** A PNG's DECLARED width and height, read from its IHDR chunk without decoding (the 8-byte
 *  signature, the first chunk's length and type, then width and height as big-endian uint32), or
 *  null when the bytes do not start with a PNG signature and an IHDR chunk. */
export function pngDeclaredSize(bytes: Buffer): { width: number; height: number } | null {
  if (bytes.length < 24) return null;
  for (let i = 0; i < 8; i++) if (bytes[i] !== PNG_SIGNATURE[i]) return null;
  if (bytes.toString('ascii', 12, 16) !== 'IHDR') return null;
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

export function loadAmbientSprites(distRoot: string): AmbientSpriteSheet[] {
  const dir = path.join(distRoot, 'assets', AMBIENT_SPRITE_DIR);
  if (!fs.existsSync(dir)) return [];
  const folders = fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort();
  const sheets: AmbientSpriteSheet[] = [];
  for (const folder of folders) {
    if (sheets.length >= AMBIENT_SPRITE_SHEETS_MAX) {
      console.warn(`[Pixel Agents] Ambient sheets over the bound (${AMBIENT_SPRITE_SHEETS_MAX}); the rest are skipped`);
      break;
    }
    try {
      const manifestPath = path.join(dir, folder, 'manifest.json');
      const pngPath = path.join(dir, folder, `${folder}.png`);
      if (!fs.existsSync(manifestPath) || !fs.existsSync(pngPath)) throw new Error('manifest or sheet missing');
      if (fs.statSync(manifestPath).size > AMBIENT_SPRITE_MANIFEST_MAX_BYTES) throw new Error('manifest over the size bound');
      if (fs.statSync(pngPath).size > AMBIENT_SPRITE_PNG_MAX_BYTES) throw new Error('sheet over the size bound');
      const m = JSON.parse(fs.readFileSync(manifestPath, 'utf-8')) as Record<string, unknown>;
      const why = checkManifest(m, folder);
      if (why) throw new Error(why);
      const fw = m.frameWidth as number;
      const fh = m.frameHeight as number;
      const cols = (m.columns as unknown[]).length;
      const bytes = fs.readFileSync(pngPath);
      const declared = pngDeclaredSize(bytes);
      if (!declared) throw new Error('not a PNG (no IHDR header)');
      if (declared.width !== cols * fw || declared.height !== 4 * fh)
        throw new Error(`sheet declares ${declared.width}x${declared.height}, expected ${cols * fw}x${4 * fh}; not decoded`);
      const png = PNG.sync.read(bytes);
      if (png.width !== cols * fw || png.height !== 4 * fh) throw new Error(`sheet is ${png.width}x${png.height}, expected ${cols * fw}x${4 * fh}`);
      const frames: string[][][][] = [];
      for (let row = 0; row < 4; row++) {
        const rowFrames: string[][][] = [];
        for (let col = 0; col < cols; col++) {
          const sprite: string[][] = [];
          for (let y = 0; y < fh; y++) {
            const line: string[] = [];
            for (let x = 0; x < fw; x++) {
              const i = ((row * fh + y) * png.width + (col * fw + x)) * 4;
              line.push(rgbaToHex(png.data[i], png.data[i + 1], png.data[i + 2], png.data[i + 3]));
            }
            sprite.push(line);
          }
          rowFrames.push(sprite);
        }
        frames.push(rowFrames);
      }
      sheets.push({
        id: m.id as string,
        label: m.label as string,
        frameWidth: fw,
        frameHeight: fh,
        rows: [...(m.rows as string[])],
        walk: [...(m.walk as number[])],
        idle: [...(m.idle as number[])],
        gesture: [...(m.gesture as number[])],
        frames,
      });
    } catch (err) {
      console.warn(`[Pixel Agents] Ambient sheet ${folder} skipped: ${err instanceof Error ? err.message : err}`);
    }
  }
  return sheets;
}
