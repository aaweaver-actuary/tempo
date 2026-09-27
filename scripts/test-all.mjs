import { spawnSync } from "node:child_process";
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { platform, release, arch } from "node:os";
import { performance } from "node:perf_hooks";
import { resolvePython } from "./resolve-python.mjs";

function protectRegressionSuite(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory() && entry.name !== "__pycache__") protectRegressionSuite(path);
    else if (/\.(tsx?|py)$/.test(path) && /\b(?:test|it|describe)\.(?:skip|todo|only)\s*\(|pytest\.mark\.skip|@(?:unittest\.)?skip/.test(readFileSync(path, "utf8"))) {
      throw new Error(`Regression suites cannot contain skipped, todo, or exclusive tests: ${path}`);
    }
  }
}

function version(command, args) {
  const result = spawnSync(command, args, { encoding: "utf8" });
  return result.status === 0 ? result.stdout.trim() : null;
}

protectRegressionSuite("tests");
protectRegressionSuite("backend/tests");
const python = resolvePython();
const stages = [
  ["unit", "npm", ["run", "test:unit"]],
  ["defense_engine", "node", ["scripts/test-defense-engine.mjs"]],
  ["backend", python, ["-m", "pytest", "backend/tests", "-q", "-o", "cache_dir=.pytest_cache", "--rootdir=."]],
  ["rust_format", "cargo", ["fmt", "--all", "--", "--check"]],
  ["rust_lint", "cargo", ["clippy", "--all-targets", "--", "-D", "warnings"]],
  ["rust_test", "cargo", ["test", "--workspace"]],
  ["lint", "npm", ["run", "lint"]],
  ["typecheck", "npm", ["run", "typecheck"]],
  ["wasm_build", "npm", ["run", "build:wasm"]],
  ["local_build", "npm", ["run", "build:local"]],
  ["browser", "npm", ["run", "test:browser"]],
  ["docker", "npm", ["run", "test:docker"]],
  ["visual", "npm", ["run", "test:visual"]],
];
const tier = process.argv[2] ?? "full";
const selectedStages = tier === "fast" ? stages.filter(([name]) => ["unit", "backend", "rust_test"].includes(name)) : tier === "integration" ? stages.filter(([name]) => ["defense_engine", "backend", "rust_format", "rust_lint", "rust_test"].includes(name)) : tier === "full" ? stages : null;
if (!selectedStages) throw new Error(`Unknown test tier: ${tier}`);
const commit = version("git", ["rev-parse", "HEAD"]);
const report = {
  schema_version: 1,
  tier,
  commit,
  timestamp: new Date().toISOString(),
  environment: { platform: platform(), release: release(), architecture: arch(), node: process.version, python: version(python, ["--version"]), rust: version("rustc", ["--version"]) },
  stages: {},
};
const outputDirectory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
function saveReport() {
  mkdirSync(outputDirectory, { recursive: true });
  const outputPath = join(outputDirectory, `test-stages-${tier}.json`);
  writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`);
  console.log(`Test stage timings: ${outputPath}`);
}
for (const [name, command, args] of selectedStages) {
  console.log(`\nRunning ${name}: ${command} ${args.join(" ")}`);
  const start = performance.now();
  const result = spawnSync(command, args, { stdio: "inherit", env: { ...process.env, PYTHONPATH: "backend" } });
  report.stages[name] = { duration_seconds: Math.round((performance.now() - start) / 10) / 100, exit_code: result.status, error: result.error?.message ?? null };
  saveReport();
  if (result.error) console.error(`Required check could not start: ${result.error.message}`);
  if (result.status !== 0 || result.error) process.exit(result.status ?? 1);
}
