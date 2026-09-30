import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { createIsolatedTestEnvironment } from "./test-environment.mjs";

const apiPort = 42_000 + 2 * (process.pid % 10_000);
const uiPort = apiPort + 1;
const apiUrl = `http://127.0.0.1:${apiPort}`;
const dockerUrl = process.env.TEMPO_DOCKER_URL;
const env = createIsolatedTestEnvironment(process.env, {
  TEMPO_BROWSER_API_PORT: String(apiPort),
  TEMPO_BROWSER_UI_PORT: String(uiPort),
  TEMPO_BROWSER_URL: dockerUrl ?? `http://127.0.0.1:${uiPort}`,
  TEMPO_BROWSER_API_URL: dockerUrl ? `${dockerUrl}/api` : `${apiUrl}/api`,
  TEMPO_BROWSER_BUILD_DIR: resolve(`test-results/browser-build-${process.pid}`),
  TEMPO_TEST_OUTPUT_DIR: process.env.TEMPO_TEST_OUTPUT_DIR ?? resolve(`test-results/browser-results-${process.pid}`),
});
const result = spawnSync("playwright", ["test", ...process.argv.slice(2)], {
  stdio: "inherit", env,
});
if (result.error) {
  console.error(result.error);
  process.exit(1);
}
process.exit(result.status ?? 1);
