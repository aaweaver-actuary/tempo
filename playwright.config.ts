import { defineConfig } from "@playwright/test";
const postgresTestUrl = process.env.TEMPO_DOCKER_URL;
if (!postgresTestUrl) {
  throw new Error("Regular Playwright tests require an isolated PostgreSQL stack; use make browser or scripts/test-postgres-docker.mjs");
}
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
    baseURL: postgresTestUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
