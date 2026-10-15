import { defineConfig } from '@playwright/test';
import path from 'path';

/**
 * The DI office usability spec runs on its OWN config: it needs neither the VS Code
 * download in e2e/global-setup.ts nor the standalone CLI. It drives the PRODUCTION
 * BUILD (dist/di-cli.js + dist/webview) against a labelled FIXTURE backend stand-in.
 * Not part of the e2e suite (e2e/tests) or its inventory.
 */
export default defineConfig({
  testDir: __dirname,
  testMatch: /.*\.spec\.ts$/,
  timeout: 180_000,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir:
    process.env['DI_E2E_OUTPUT_DIR'] ?? path.join(__dirname, '../../test-results/di-office'),
  use: {
    browserName: 'chromium',
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 2,
  },
});
