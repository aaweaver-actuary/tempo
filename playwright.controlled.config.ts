import { defineConfig } from "@playwright/test";
const origin = process.env.TEMPO_CONTROLLED_ORIGIN;
if (process.env.TEMPO_CONTROLLED_COLLECTION !== "1" && (!origin || !process.env.TEMPO_CONTROLLED_RUN_ID || !/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)))
  throw new Error("Use scripts/test-controlled-browser.mjs for an invocation-owned host");
export default defineConfig({
  testDir: "tests/controlled-browser", testMatch: "*.spec.ts",
  retries: 0, timeout: 60_000, fullyParallel: false, workers: 1, forbidOnly: true,
  updateSnapshots: "none",
  outputDir: process.env.TEMPO_CONTROLLED_OUTPUT_DIR,
  reporter: [["line"], ["json", { outputFile: process.env.TEMPO_CI_REPORT }]],
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "firefox", testMatch: "interaction.spec.ts", use: { browserName: "firefox" } },
    { name: "webkit", testMatch: "interaction.spec.ts", use: { browserName: "webkit" } },
  ],
  use: { baseURL: origin, timezoneId: "America/New_York", serviceWorkers: "block", trace: "retain-on-failure", screenshot: "only-on-failure" },
});
