import { randomUUID } from "node:crypto";
import { cpus, totalmem, tmpdir } from "node:os";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { withTaskBrowserBuild } from "./task-browser-build.mjs";
import { pinnedPlaywrightImage } from "./pinned-playwright-image.mjs";
import { recordBudgetResource, runWithDevelopmentBudget, validateDevelopmentBudgetOwner, waitForStudyQuiet } from "./development-budget.mjs";
import { managedDockerEnvironment } from "./development-docker.mjs";

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
if (!process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN) {
  process.exitCode = await runWithDevelopmentBudget(process.execPath, process.argv.slice(1));
} else {
await validateDevelopmentBudgetOwner();
await waitForStudyQuiet();
const wrapperDirectory = mkdtempSync(join(tmpdir(), "tempo-visual-docker-"));
try {
const env = managedDockerEnvironment(process.env, wrapperDirectory);
const commitResult = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
const commit = process.env.TEMPO_FULL_TEST_RUN_COMMIT ?? process.env.GITHUB_SHA ?? (commitResult.status === 0 ? commitResult.stdout.trim() : "unknown");
// Cover cancellation before the managed adapter can journal its container.
// Unknown work deliberately requires owner-directed reconciliation.
const rehearsalResource = `rehearsal:visual-${randomUUID()}`;
await recordBudgetResource(rehearsalResource, true);
const dockerCapacity = spawnSync("docker", ["info", "--format", "{{json .NCPU}} {{json .MemTotal}}"], { encoding: "utf8", env });
const [diagnosticCpus, diagnosticMemory] = dockerCapacity.status === 0 ? dockerCapacity.stdout.trim().split(" ") : [];
const hostDetails = { platform: process.platform, architecture: process.arch,
  cpuModel: cpus()[0]?.model ?? "unavailable", logicalCpus: cpus().length, memoryBytes: totalmem() };
const timingDirectory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
mkdirSync(timingDirectory, { recursive: true });
writeFileSync(join(timingDirectory, "performance-run.json"), JSON.stringify({
  schema_version: 1, commit, timestamp: process.env.TEMPO_FULL_TEST_RUN_TIMESTAMP ?? new Date().toISOString(), stages: {},
  environment: { platform: process.platform, architecture: process.arch,
    dockerCpus: diagnosticCpus ?? null, dockerMemoryBytes: diagnosticMemory ?? null,
    host: hostDetails, runner: "linux-pinned", buildMode: "production-local" },
}, null, 2) + "\n");
mkdirSync("test-results", { recursive: true });
const result = withTaskBrowserBuild((output) => spawnSync(
  "docker",
  [
    "run",
    "--platform",
    "linux/arm64",
    "--rm",
    "--init",
    "--ipc=host",
    "--memory", "2048m",
    "--pids-limit", "512",
    "-e",
    "TEMPO_VISUAL_RUNNER=linux-pinned",
    "-e", `TEMPO_BROWSER_BUILD_DIR=/workspace/${relative(process.cwd(), output).split(sep).join("/")}`,
    "-e",
    `TEMPO_COMMIT=${commit}`,
    "-e", `TEMPO_DIAGNOSTIC_DOCKER_CPUS=${diagnosticCpus ?? "unavailable"}`,
    "-e", `TEMPO_DIAGNOSTIC_DOCKER_MEMORY=${diagnosticMemory ?? "unavailable"}`,
    "-e", `TEMPO_DIAGNOSTIC_HOST=${JSON.stringify(hostDetails)}`,
    "-e", "TEMPO_DIAGNOSTIC_CONTAINER_LIMITS=memory=2048 MiB; pids=512; shared host IPC; unrestricted CPU; trial allocation, not prequalified",
    ...(containerTimingDirectory ? ["-e", `TEMPO_TEST_TIMING_DIR=${containerTimingDirectory}`] : []),
    ...(process.env.CI ? ["-e", "CI=true"] : []),
    ...(process.env.TEMPO_CI_REPORT ? ["-e", `TEMPO_CI_REPORT=${process.env.TEMPO_CI_REPORT}`] : []),
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
  { stdio: "inherit", env },
), { parent: resolve("test-results"), target: "local" });
if (!dockerCapacity.error && !dockerCapacity.signal && Number.isInteger(dockerCapacity.status) &&
    !result.error && !result.signal && Number.isInteger(result.status))
  await recordBudgetResource(rehearsalResource, false);
if (result.error) console.error(result.error.message);
process.exitCode = result.status ?? 1;
} finally {
  rmSync(wrapperDirectory, { recursive: true, force: true });
}
}
