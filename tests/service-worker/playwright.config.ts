import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.', testMatch: 'upgrade-review.spec.ts', timeout: 60_000,
  workers: 1, retries: 0, reporter: 'line',
  outputDir: process.env.TEMPO_TEST_OUTPUT_DIR,
  use: { browserName: 'chromium', serviceWorkers: 'allow', trace: 'retain-on-failure' },
});
