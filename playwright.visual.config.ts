import { defineConfig } from "@playwright/test";
if (process.env.TEMPO_VISUAL_RUNNER !== "linux-pinned")
  throw new Error(
    "Run npm run test:visual to use the pinned Linux browser environment.",
  );
export default defineConfig({
  outputDir: "test-results/visual",
  testDir: "tests/browser",
  testMatch: ["visual.spec.ts", "performance.spec.ts"],
  workers: 1,
  timeout: 60000,
  updateSnapshots: "none",
  snapshotPathTemplate: "{testDir}/visual-baselines/{arg}{ext}",
  use: {
    baseURL: "http://127.0.0.1:3001",
    browserName: "chromium",
    locale: "en-US",
    timezoneId: "America/New_York",
    reducedMotion: "reduce",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.001 } },
  webServer: {
    command: "npm run build:local && TEMPO_TARGET=local vite preview --config vite.static.config.ts --host 0.0.0.0 --port 3001 --strictPort",
    url: "http://127.0.0.1:3001",
    reuseExistingServer: false,
    timeout: 120000,
  },
});
