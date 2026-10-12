import { defineConfig } from "@playwright/test";
const controlledOrigin = process.env.TEMPO_CONTROLLED_ORIGIN;
if (!controlledOrigin || !process.env.TEMPO_CONTROLLED_RUN_ID || !/^http:\/\/127\.0\.0\.1:\d+$/.test(controlledOrigin)) {
  throw new Error("Use scripts/test-controlled-browser.mjs; the controlled host must be invocation-owned");
}
process.env.TZ = "America/New_York";
export default defineConfig({
  testDir: "tests/controlled-browser", testMatch: "*.spec.ts",
  retries: 0, timeout: 60_000, fullyParallel: false, workers: 1, forbidOnly: true,
  outputDir: process.env.TEMPO_CONTROLLED_OUTPUT_DIR,
  reporter: [["line"], ["json", { outputFile: process.env.TEMPO_CI_REPORT }], ["./scripts/browser-timing-reporter.mjs"]],
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "firefox", testMatch: "interaction.spec.ts", use: { browserName: "firefox" } },
    { name: "webkit", testMatch: "interaction.spec.ts", use: { browserName: "webkit" } },
  ],
  use: { baseURL: controlledOrigin, timezoneId: "America/New_York", trace: "retain-on-failure", screenshot: "only-on-failure" },
});
