import { defineConfig } from "@playwright/test";
const postgresTestUrl = process.env.TEMPO_DOCKER_URL;
if (!postgresTestUrl) {
  throw new Error("Regular Playwright tests require an isolated PostgreSQL stack; use make browser or scripts/test-postgres-docker.mjs");
}
export default defineConfig({
  retries: 0,
  reporter: process.env.TEMPO_CI_REPORT ? [["line"], ["json", { outputFile: process.env.TEMPO_CI_REPORT }]] : undefined,
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
    // Match the disposable PostgreSQL API's calendar day on every runner host.
    timezoneId: "America/New_York",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
