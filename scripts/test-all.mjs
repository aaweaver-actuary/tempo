import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { platform, release, arch } from "node:os";
import { performance } from "node:perf_hooks";
import { protectRegressionSuite, verificationStages } from "./verification-stages.mjs";
import { resolvePython } from "./resolve-python.mjs";
import { assertNoTrackedConflicts } from "./check-merge-conflicts.mjs";


function version(command, args) {
  const result = spawnSync(command, args, { encoding: "utf8" });
  return result.status === 0 ? result.stdout.trim() : null;
}

assertNoTrackedConflicts();
protectRegressionSuite("tests");
protectRegressionSuite("backend/tests");
const listOnly = process.argv[2] === "--list";
const tier = (listOnly ? process.argv[3] : process.argv[2]) ?? "full";
const outputDirectory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
const unitProfilePath = join(outputDirectory, `unit-files-${tier}.json`);
const python = resolvePython();
const { stages, stagesByTier } = verificationStages({ python, tier, outputDirectory });
const stageNames = stagesByTier[tier];
const selectedStages = stageNames
  ? stages.filter(([name]) => stageNames.includes(name)).map(([name, command, args]) => [
    name, command,
    name === "postgres_docker" && ["ui", "browser"].includes(tier)
      ? [...args, "--mode", "browser"] : args,
  ])
  : null;
if (!selectedStages) throw new Error(`Unknown test tier: ${tier}`);
if (listOnly) {
  console.log(JSON.stringify({ tier, stages: selectedStages.map(([name, command, args]) => ({ name, command, args })) }, null, 2));
  process.exit(0);
}
const commit = version("git", ["rev-parse", "HEAD"]);
const report = {
  schema_version: 1,
  tier,
  commit,
  timestamp: new Date().toISOString(),
  environment: { platform: platform(), release: release(), architecture: arch(), node: process.version, python: version(python, ["--version"]), rust: version("rustc", ["--version"]) },
  stages: {},
};
function saveReport() {
  mkdirSync(outputDirectory, { recursive: true });
  const outputPath = join(outputDirectory, `test-stages-${tier}.json`);
  writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`);
  console.log(`Test stage timings: ${outputPath}`);
}
for (const [name, command, args] of selectedStages) {
  console.log(`\nRunning ${name}: ${command} ${args.join(" ")}`);
  if (name === "unit") {
    mkdirSync(outputDirectory, { recursive: true });
    rmSync(unitProfilePath, { force: true });
  }
  const start = performance.now();
  const result = spawnSync(command, args, { stdio: "inherit", env: { ...process.env, PYTHONPATH: "backend",
    ...(tier === "full" ? { TEMPO_FULL_TEST_RUN_TIMESTAMP: report.timestamp, TEMPO_FULL_TEST_RUN_COMMIT: report.commit } : {}) } });
  report.stages[name] = { duration_seconds: Math.round((performance.now() - start) / 10) / 100, exit_code: result.status, error: result.error?.message ?? null };
  saveReport();
  if (result.error) console.error(`Required check could not start: ${result.error.message}`);
  if (result.status !== 0 || result.error) process.exit(result.status ?? 1);
}
