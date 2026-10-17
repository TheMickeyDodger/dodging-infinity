import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vitest/config';

process.env['ALLURE_LABEL_epic'] ??= 'server';

// Resolved from THIS file (as webview-ui/vitest.config.ts does), not from the working directory:
// a run started anywhere else (e.g. `vitest run --root server` from the package root) must still
// write inside this package's ignored allure-results/, never beside it.
const rootDir = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  test: {
    globals: true,
    testTimeout: 10_000,
    include: ['__tests__/**/*.test.ts'],
    setupFiles: ['allure-vitest/setup'],
    reporters: [
      'default',
      [
        'allure-vitest/reporter',
        {
          resultsDir: path.resolve(rootDir, '../allure-results/server'),
        },
      ],
    ],
  },
});
