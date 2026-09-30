import { spawnSync } from "node:child_process";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { pinnedPlaywrightImage } from "./pinned-playwright-image.mjs";
const update = process.argv.includes("--update");
const performanceOnly = process.argv.includes("--performance-only");
if (update && performanceOnly) throw new Error("Performance-only runs cannot update visual baselines.");
if (update && process.env.CI)
  throw new Error("Visual baselines cannot be updated in CI.");
let containerTimingDirectory;
if (process.env.TEMPO_TEST_TIMING_DIR) {
  const relativeTimingDirectory = relative(process.cwd(), resolve(process.env.TEMPO_TEST_TIMING_DIR));
  if (!relativeTimingDirectory || relativeTimingDirectory === ".." ||
      relativeTimingDirectory.startsWith(`..${sep}`) || isAbsolute(relativeTimingDirectory))
    throw new Error("TEMPO_TEST_TIMING_DIR must be inside the checkout for pinned Docker runs.");
  containerTimingDirectory = `/workspace/${relativeTimingDirectory.split(sep).join("/")}`;
}
const commitResult = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
const commit = process.env.GITHUB_SHA ?? (commitResult.status === 0 ? commitResult.stdout.trim() : "unknown");
const dockerCapacity = spawnSync("docker", ["info", "--format", "{{json .NCPU}} {{json .MemTotal}}"], { encoding: "utf8" });
const [diagnosticCpus, diagnosticMemory] = dockerCapacity.status === 0 ? dockerCapacity.stdout.trim().split(" ") : [];
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
    "-e", `TEMPO_DIAGNOSTIC_DOCKER_CPUS=${diagnosticCpus ?? "unavailable"}`,
    "-e", `TEMPO_DIAGNOSTIC_DOCKER_MEMORY=${diagnosticMemory ?? "unavailable"}`,
    "-e", `TEMPO_DIAGNOSTIC_HOST=${process.platform}/${process.arch}`,
    "-e", "TEMPO_DIAGNOSTIC_CONTAINER_LIMITS=no explicit per-container CPU/memory limits; shared Docker VM",
    ...(containerTimingDirectory ? ["-e", `TEMPO_TEST_TIMING_DIR=${containerTimingDirectory}`] : []),
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
