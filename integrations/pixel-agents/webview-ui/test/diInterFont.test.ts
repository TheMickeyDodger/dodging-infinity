/**
 * A1 (amended): Inter, the Home face, is SUPPLIED with the app, offline, and declared the way the FS Pixel Sans face
 * already is (index.css @font-face, served from webview-ui/public/fonts). No runtime third-party font: nothing is fetched
 * from another origin. The file is the official Inter 4.1 release asset, byte for byte, carried with its SIL OFL 1.1
 * licence.
 *
 * WHAT THIS FILE PROVES: the bytes on disk, the licence beside them, and the declaration. It does NOT prove that Inter
 * is the face that paints: diInterRendered.test.ts proves that in a real browser, and says what it does not prove.
 *
 * The digest is the official release's (https://github.com/rsms/inter/releases/tag/v4.1, Inter-4.1.zip,
 * web/InterVariable.woff2), written here by hand when the file was vendored.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FONT = path.join(root, 'public/fonts/InterVariable.woff2');
const LICENCE = path.join(root, 'public/fonts/Inter-OFL.txt');
const INTER_4_1_VARIABLE_SHA256 =
  '693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3';

const code = (file: string) => fs.readFileSync(file, 'utf-8').replace(/\/\*[\s\S]*?\*\//g, '');
const fontFaces = (css: string) => [...css.matchAll(/@font-face\s*\{([^}]*)\}/g)].map((m) => m[1]);

describe('the bundled Inter is the official release file, with its licence', () => {
  it('is Inter 4.1 InterVariable.woff2, byte for byte, and a WOFF2 file', () => {
    const bytes = fs.readFileSync(FONT);
    expect(bytes.subarray(0, 4).toString('latin1')).toBe('wOF2');
    expect(crypto.createHash('sha256').update(bytes).digest('hex')).toBe(INTER_4_1_VARIABLE_SHA256);
  });
  it('carries the SIL Open Font License 1.1 and the Inter copyright beside it', () => {
    const licence = fs.readFileSync(LICENCE, 'utf-8');
    expect(licence).toContain(
      'Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter)',
    );
    expect(licence).toContain(
      'This Font Software is licensed under the SIL Open Font License, Version 1.1.',
    );
    expect(licence).toContain('SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007');
  });
});

describe('the Inter face is declared like the pixel face, and loads nothing from the network', () => {
  const css = code(path.join(root, 'src/index.css'));
  it('index.css declares Inter once, from the bundled file only, for every weight the chrome uses', () => {
    const inter = fontFaces(css).filter((f) => /font-family:\s*'Inter'/.test(f));
    expect(inter).toHaveLength(1);
    expect(inter[0]).toMatch(/src:\s*url\('\/fonts\/InterVariable\.woff2'\)\s*format\('woff2'\);/);
    expect(inter[0]).toMatch(/font-weight:\s*100 900;/);
    expect(inter[0]).toMatch(/font-style:\s*normal;/);
    // the precedent: the pixel face is served from the same place
    expect(
      fontFaces(css).some((f) => /url\('\/fonts\/FSPixelSansUnicode-Regular\.ttf'\)/.test(f)),
    ).toBe(true);
  });
  it('no stylesheet or page imports a font or stylesheet from another origin', () => {
    const files = [
      path.join(root, 'index.html'),
      path.join(root, 'src/index.css'),
      ...fs
        .readdirSync(path.join(root, 'src/components'))
        .filter((f) => f.endsWith('.css'))
        .map((f) => path.join(root, 'src/components', f)),
    ];
    for (const file of files) {
      const text = code(file);
      expect(text, file).not.toMatch(
        /fonts\.googleapis|fonts\.gstatic|@import\s+url\(\s*['"]?https?:/,
      );
      for (const face of fontFaces(text))
        expect(face, file).not.toMatch(/url\(\s*['"]?(?:https?:)?\/\//);
      expect(text, file).not.toMatch(/<link[^>]+href="https?:\/\/[^"]*font/i);
    }
  });
});
