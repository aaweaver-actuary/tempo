import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/browser", timeout: 60_000, fullyParallel: false, workers: 1, forbidOnly: true,
  use: { baseURL: process.env.TEMPO_DOCKER_URL ?? "http://127.0.0.1:3001", trace: "retain-on-failure" },
  webServer: process.env.TEMPO_DOCKER_URL ? undefined : [
    { command: "node scripts/test-api.mjs", url: "http://127.0.0.1:8001/api/health", reuseExistingServer: false },
    { command: "npx vite --config vite.static.config.ts --host 127.0.0.1 --port 3001 --strictPort", env: { TEMPO_TARGET: "local", TEMPO_PROXY_API: "http://127.0.0.1:8001" }, url: "http://127.0.0.1:3001", timeout: 120_000, reuseExistingServer: false },
  ],
});
