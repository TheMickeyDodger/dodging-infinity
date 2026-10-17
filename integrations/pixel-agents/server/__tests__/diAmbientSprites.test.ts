/**
 * DI office: the AMBIENT CHARACTER SHEETS loader and message (M3 S6). The bundled sheets load whole
 * (10: five jobs, five Easter-egg families) with their exact geometry; any sheet failing a bound
 * (manifest, size, frame geometry, facing rows, indices, dimensions) is skipped whole, never partly
 * sent; at most AMBIENT_SPRITE_SHEETS_MAX are sent; the handshake carries them before the layout.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { PNG } from 'pngjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentStateStore } from '../src/agentStateStore.js';
import { AMBIENT_SPRITE_PNG_MAX_BYTES, AMBIENT_SPRITE_SHEETS_MAX } from '../src/constants.js';
import { loadAmbientSprites, pngDeclaredSize } from '../src/di/ambientSprites.js';
import { sendDiHandshake } from '../src/di/diClientMessages.js';
import { resetStateRootForTests, setStateRoot } from '../src/stateRoot.js';

const BUNDLED = path.join(__dirname, '..', '..', 'webview-ui', 'public');
let sandbox: string;
beforeEach(() => {
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-ambient-sprites-')));
  resetStateRootForTests();
  setStateRoot(path.join(sandbox, 'root'));
});
afterEach(() => {
  resetStateRootForTests();
  fs.rmSync(sandbox, { recursive: true, force: true });
});

function plant(id: string, manifest: Record<string, unknown>, w: number, h: number, extraBytes = 0): void {
  const dir = path.join(sandbox, 'dist', 'assets', 'ambient', id);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest));
  const png = new PNG({ width: w, height: h });
  const buf = PNG.sync.write(png);
  fs.writeFileSync(path.join(dir, `${id}.png`), extraBytes ? Buffer.concat([buf, Buffer.alloc(extraBytes)]) : buf);
}
const good = (id: string, over: Record<string, unknown> = {}) => ({ id, label: 'Test', frameWidth: 16, frameHeight: 32, rows: ['DOWN', 'LEFT', 'RIGHT', 'UP'], columns: ['w1', 'w2', 'i1', 'g1'], walk: [0, 1], idle: [2], gesture: [3], ...over });

describe('bundled sheets', () => {
  it('load whole: 10 sheets with their geometry, four named rows, every index inside the columns', () => {
    const sheets = loadAmbientSprites(BUNDLED);
    expect(sheets.map((s) => s.id)).toEqual(['barista', 'bellhop', 'egg-cul-de-sac-kid', 'egg-martial-artist', 'egg-pocket-creature', 'egg-shinobi', 'egg-trickster-hare', 'firefighter', 'police', 'store-clerk']);
    for (const s of sheets) {
      expect(s.frames).toHaveLength(4);
      const cols = s.frames[0].length;
      for (const row of s.frames) {
        expect(row).toHaveLength(cols);
        for (const f of row) {
          expect(f).toHaveLength(s.frameHeight);
          expect(f[0]).toHaveLength(s.frameWidth);
        }
      }
      for (const i of [...s.walk, ...s.idle, ...s.gesture]) expect(i).toBeLessThan(cols);
      // a frame is never empty art
      expect(s.frames[0][s.walk[0]].flat().some((p) => p !== '')).toBe(true);
    }
  });
});

describe('bounds: a failing sheet is skipped whole', () => {
  it('admits a good sheet and skips each malformed one', () => {
    plant('ok', good('ok'), 64, 128);
    plant('wrong-id', good('other'), 64, 128);
    plant('three-rows', good('three-rows', { rows: ['DOWN', 'LEFT', 'RIGHT'] }), 64, 128);
    plant('big-frame', good('big-frame', { frameWidth: 64, frameHeight: 64 }), 256, 256);
    plant('bad-index', good('bad-index', { gesture: [9] }), 64, 128);
    plant('wrong-size', good('wrong-size'), 60, 128);
    plant('huge-file', good('huge-file'), 64, 128, AMBIENT_SPRITE_PNG_MAX_BYTES + 1);
    const sheets = loadAmbientSprites(path.join(sandbox, 'dist'));
    expect(sheets.map((s) => s.id)).toEqual(['ok']);
  });

  it('a sheet whose IHDR DECLARES oversized dimensions is refused with its reason, and is never decoded', () => {
    // a small, valid-looking file whose header claims 40000 x 40000 (6.4 GB of RGBA if decoded)
    plant('liar', good('liar'), 64, 128);
    plant('ok', good('ok'), 64, 128); // the control: the spy must SEE this decode, or it proves nothing
    const file = path.join(sandbox, 'dist', 'assets', 'ambient', 'liar', 'liar.png');
    const bytes = fs.readFileSync(file);
    bytes.writeUInt32BE(40_000, 16);
    bytes.writeUInt32BE(40_000, 20);
    fs.writeFileSync(file, bytes);
    expect(pngDeclaredSize(bytes)).toEqual({ width: 40_000, height: 40_000 });
    const warnings: string[] = [];
    const warn = vi.spyOn(console, 'warn').mockImplementation((m: string) => void warnings.push(m));
    const decode = vi.spyOn(PNG.sync, 'read');
    try {
      expect(loadAmbientSprites(path.join(sandbox, 'dist')).map((x) => x.id)).toEqual(['ok']);
      expect(decode).toHaveBeenCalledTimes(1); // the good sheet only: the liar was never decoded
      expect(warnings.join('\n')).toMatch(/liar skipped: sheet declares 40000x40000, expected 64x128; not decoded/);
    } finally {
      decode.mockRestore();
      warn.mockRestore();
    }
  });

  it('sends at most AMBIENT_SPRITE_SHEETS_MAX sheets', () => {
    for (let k = 0; k < AMBIENT_SPRITE_SHEETS_MAX + 3; k++) plant(`s${String(k).padStart(2, '0')}`, good(`s${String(k).padStart(2, '0')}`), 64, 128);
    expect(loadAmbientSprites(path.join(sandbox, 'dist'))).toHaveLength(AMBIENT_SPRITE_SHEETS_MAX);
  });

  it('no ambient folder: no sheets, no error', () => {
    expect(loadAmbientSprites(path.join(sandbox, 'nothing'))).toEqual([]);
  });
});

describe('handshake', () => {
  it('ambientSpritesLoaded is sent once, before ambientStateLoaded and layoutLoaded; absent when there are no sheets', () => {
    const store = new AgentStateStore();
    const cache = { characters: null, pets: null, floorTiles: null, wallTiles: null, carpetTiles: null, furniture: null, defaultLayout: null };
    const sheets = loadAmbientSprites(BUNDLED);
    const sent: Array<Record<string, unknown>> = [];
    sendDiHandshake((m) => sent.push(m), { store, cache: cache as never, extensionVersion: 'x', ambientSprites: sheets });
    const types = sent.map((m) => m.type);
    expect(types.filter((t) => t === 'ambientSpritesLoaded')).toHaveLength(1);
    expect(types.indexOf('ambientSpritesLoaded')).toBeLessThan(types.indexOf('ambientStateLoaded'));
    expect(types.indexOf('ambientStateLoaded')).toBeLessThan(types.indexOf('layoutLoaded'));
    const none: Array<Record<string, unknown>> = [];
    sendDiHandshake((m) => none.push(m), { store, cache: cache as never, extensionVersion: 'x' });
    expect(none.some((m) => m.type === 'ambientSpritesLoaded')).toBe(false);
  });
});
