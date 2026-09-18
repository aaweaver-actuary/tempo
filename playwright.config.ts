import { defineConfig } from "@playwright/test";
export default defineConfig({
  outputDir: "test-results/browser",
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
    baseURL: process.env.TEMPO_DOCKER_URL ?? "http://127.0.0.1:3001",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: process.env.TEMPO_DOCKER_URL
    ? undefined
    : [
        {
          command: "node scripts/test-api.mjs",
          url: "http://127.0.0.1:8001/api/health",
          reuseExistingServer: false,
        },
        {
          command:
            "npm run build:local && TEMPO_TARGET=local vite preview --config vite.static.config.ts --host 127.0.0.1 --port 3001 --strictPort",
          env: { TEMPO_PROXY_API: "http://127.0.0.1:8001" },
          url: "http://127.0.0.1:3001",
          timeout: 120_000,
          reuseExistingServer: false,
        },
      ],
});
