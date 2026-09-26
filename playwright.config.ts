import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";
const apiPort = process.env.TEMPO_BROWSER_API_PORT ?? "8001";
const uiPort = process.env.TEMPO_BROWSER_UI_PORT ?? "3001";
const apiUrl = `http://127.0.0.1:${apiPort}`;
const uiUrl = `http://127.0.0.1:${uiPort}`;
export default defineConfig({
  outputDir: process.env.TEMPO_TEST_OUTPUT_DIR ?? "test-results/browser",
  testDir: "tests/browser",
  testIgnore: ["visual.spec.ts", "performance.spec.ts"],
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    {
      name: "firefox",
      testMatch: "cross-browser.spec.ts",
      use: { browserName: "firefox" },
    },
    {
      name: "webkit",
      testMatch: "cross-browser.spec.ts",
      use: { browserName: "webkit" },
    },
  ],
  use: {
    baseURL: process.env.TEMPO_DOCKER_URL ?? uiUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: process.env.TEMPO_DOCKER_URL
    ? undefined
    : [
        {
          command: "node scripts/test-api.mjs",
          env: { TEMPO_BROWSER_API_PORT: apiPort },
          url: `${apiUrl}/api/health`,
          reuseExistingServer: false,
        },
        {
          command:
            `npm run build:local && TEMPO_TARGET=local vite preview --config vite.static.config.ts --host 127.0.0.1 --port ${uiPort} --strictPort`,
          env: { TEMPO_PROXY_API: apiUrl, TEMPO_BROWSER_BUILD_DIR: process.env.TEMPO_BROWSER_BUILD_DIR ?? resolve("test-results/browser-build-manual") },
          url: uiUrl,
          timeout: 120_000,
          reuseExistingServer: false,
        },
      ],
});
