import { spawnSync } from "node:child_process";
import { pinnedPlaywrightImage } from "./pinned-playwright-image.mjs";
const update = process.argv.includes("--update");
const performanceOnly = process.argv.includes("--performance-only");
if (update && performanceOnly) throw new Error("Performance-only runs cannot update visual baselines.");
if (update && process.env.CI)
  throw new Error("Visual baselines cannot be updated in CI.");
const commitResult = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
const commit = process.env.GITHUB_SHA ?? (commitResult.status === 0 ? commitResult.stdout.trim() : "unknown");
const result = spawnSync(
  "docker",
  [
    "run",
    "--platform",
    "linux/arm64",
    "--rm",
    "--init",
    "--ipc=host",
    "-e",
    "TEMPO_VISUAL_RUNNER=linux-pinned",
    "-e",
    `TEMPO_COMMIT=${commit}`,
    ...(process.env.CI ? ["-e", "CI=true"] : []),
    "-v",
    `${process.cwd()}:/workspace`,
    "-v",
    "/workspace/node_modules",
    "--mount",
    "type=volume,source=tempo-playwright-npm-cache,target=/root/.npm",
    "-w",
    "/workspace",
    pinnedPlaywrightImage,
    "bash",
    "-lc",
    `npm ci --no-audit && npx playwright test --config playwright.visual.config.ts${performanceOnly ? " performance.spec.ts" : ""}${update ? " --update-snapshots" : ""}`,
  ],
  { stdio: "inherit" },
);
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
