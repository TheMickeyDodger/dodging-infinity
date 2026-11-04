/**
 * Deliverable 3: the LAUNCH PATH of the isolated Mission preview (FIXTURE / VISUAL TEST ONLY). From integrations/pixel-agents/webview-ui:
 *
 *   npx vite build --config test/harness/di-mission-preview.config.ts
 *   npx vite preview --config test/harness/di-mission-preview.config.ts --port 5179 --strictPort
 *
 * then open http://localhost:5179/test/harness/di-mission-preview.html (the scene index). The build is the webview's own PRODUCTION build (the real
 * app and its assets, no development client, so the page opens no socket but the preview's own /ws) plus the scene pages; the preview server adds
 * the fixture transport (diMissionPreview.ts). No office server is started, so no --state-dir, --fixture-source, panel, delivery or Herdr flag exists
 * to pass, and nothing outside this repository's fixtures is read.
 *
 * The build goes to node_modules/.di-mission-preview (ignored), or to DI_PREVIEW_OUT_DIR (absolute); it refuses any other non-empty directory.
 * Stop the preview with Ctrl-C. Captures: DI_PREVIEW_CAPTURE_DIR=<abs dir> npx vitest run test/diMissionPreviewBrowser.test.ts
 * (docs/di-mission-preview.md).
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { defineConfig, mergeConfig } from 'vite';

import base from '../../vite.config.ts';
import { diMissionPreviewPlugin } from './diMissionPreview.ts';

const webview = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const outDir = process.env.DI_PREVIEW_OUT_DIR ?? path.join(webview, 'node_modules/.di-mission-preview');
if (!path.isAbsolute(outDir)) throw new Error('DI_PREVIEW_OUT_DIR must be an absolute directory');

export default mergeConfig(
  base,
  defineConfig({
    root: webview,
    plugins: [diMissionPreviewPlugin()],
    build: {
      outDir,
      emptyOutDir: true,
      rolldownOptions: {
        input: {
          index: path.join(webview, 'test/harness/di-mission-preview.html'),
          scene: path.join(webview, 'test/harness/di-mission-preview-scene.html'),
        },
      },
    },
  }),
);
