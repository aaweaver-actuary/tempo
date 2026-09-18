import { spawnSync } from "node:child_process";
const update = process.argv.includes("--update");
if (update && process.env.CI)
  throw new Error("Visual baselines cannot be updated in CI.");
const image =
  "mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27";
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
    ...(process.env.CI ? ["-e", "CI=true"] : []),
    "-v",
    `${process.cwd()}:/workspace`,
    "-v",
    "/workspace/node_modules",
    "-w",
    "/workspace",
    image,
    "bash",
    "-lc",
    `npm ci --no-audit && npx playwright test --config playwright.visual.config.ts${update ? " --update-snapshots" : ""}`,
  ],
  { stdio: "inherit" },
);
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
