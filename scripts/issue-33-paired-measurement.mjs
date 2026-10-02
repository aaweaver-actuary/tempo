// One-off measurement orchestration; retained only on the benchmark branch.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createWriteStream, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pinnedPlaywrightImage } from "./pinned-playwright-image.mjs";

const baseDirectory = resolve(process.env.BASE_CHECKOUT);
const candidateDirectory = resolve(process.env.CANDIDATE_CHECKOUT);
const outputDirectory = resolve("test-results/issue-33-pair");
const baseCommit = "dfbb66d67b314357e55c2030ff794a15415f316c";
const candidateCommit = "1496021f82bfddd1df8b4276fcc57f7e7dd853df";
mkdirSync(outputDirectory, { recursive: true });
const evidence = { baseCommit, candidateCommit, workflowCommit: process.env.GITHUB_SHA,
  runId: process.env.GITHUB_RUN_ID, runAttempt: process.env.GITHUB_RUN_ATTEMPT,
  nodeVersion: process.version, pinnedPlaywrightImage, singleJobSequential: true,
  commands: [], harnessHashes: {}, baselineChanges: [], comparison: null };
const save = () => writeFileSync(join(outputDirectory, "run-evidence.json"), JSON.stringify(evidence, null, 2) + "\n");
const hash = (contents) => createHash("sha256").update(contents).digest("hex");
const gitOutput = (directory, args) => {
  const result = spawnSync("git", args, { cwd: directory, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
};
async function run(label, command, args, directory, environment = {}) {
  const started = performance.now();
  const record = { label, command, args, directory, environment, startedAt: new Date().toISOString(), exitCode: null, seconds: null };
  evidence.commands.push(record); save();
  const log = createWriteStream(join(outputDirectory, `${label}.log`));
  const child = spawn(command, args, { cwd: directory, env: { ...process.env, ...environment }, stdio: ["ignore", "pipe", "pipe"] });
  child.stdout.on("data", (chunk) => { process.stdout.write(chunk); log.write(chunk); });
  child.stderr.on("data", (chunk) => { process.stderr.write(chunk); log.write(chunk); });
  const result = await new Promise((resolveResult, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolveResult({ code: code ?? 1, signal }));
  });
  await new Promise((finish) => log.end(finish));
  record.exitCode = result.code; record.signal = result.signal;
  record.seconds = (performance.now() - started) / 1000; record.finishedAt = new Date().toISOString(); save();
  return result.code;
}
async function required(label, command, args, directory, environment) {
  assert.equal(await run(label, command, args, directory, environment), 0, `${label} failed; inspect retained log`);
}

try {
  assert.equal(gitOutput(baseDirectory, ["rev-parse", "HEAD"]), baseCommit);
  assert.equal(gitOutput(candidateDirectory, ["rev-parse", "HEAD"]), candidateCommit);
  assert.equal(gitOutput(baseDirectory, ["status", "--porcelain"]), "");
  assert.equal(gitOutput(candidateDirectory, ["status", "--porcelain"]), "");
  const fixturePatch = join(candidateDirectory, "docs/measurements/issue-33/base-measurement-harness.patch");
  evidence.fixturePatchSha256 = hash(readFileSync(fixturePatch));
  await required("port-base-fixture", "git", ["apply", fixturePatch], baseDirectory);
  evidence.baselineChanges = gitOutput(baseDirectory, ["diff", "--name-only"]).split("\n");
  assert.deepEqual(evidence.baselineChanges.sort(), ["tests/browser/performance.spec.ts", "tests/unit/discoveries-tray-regressions.test.tsx"]);
  writeFileSync(join(outputDirectory, "base-fixture.patch"), gitOutput(baseDirectory, ["diff"]) + "\n");
  for (const path of ["package.json", "package-lock.json", "Makefile", "vite.static.config.ts", "playwright.visual.config.ts",
    "scripts/test-visual.mjs", "scripts/pinned-playwright-image.mjs", "scripts/check-test-capabilities.mjs",
    "tests/browser/performance.spec.ts", "tests/browser/held-drag-fixtures.ts", "tests/browser/ui-fixtures.ts", "tests/browser/visual-fixtures.ts"]) {
    const beforeHash = hash(readFileSync(join(baseDirectory, path)));
    assert.equal(hash(readFileSync(join(candidateDirectory, path))), beforeHash, `${path} differs between measurement harnesses`);
    evidence.harnessHashes[path] = beforeHash;
  }
  evidence.productTrees = {};
  for (const [label, directory] of [["before", baseDirectory], ["after", candidateDirectory]])
    evidence.productTrees[label] = gitOutput(directory, ["ls-tree", "HEAD", "app", "backend", "public"]);
  save();
  await required("plan", "make", ["plan"], candidateDirectory);
  // Create host-owned node_modules before Docker mounts anonymous dependencies.
  for (const [label, directory] of [["before", baseDirectory], ["after", candidateDirectory]])
    await required(`install-host-${label}`, "npm", ["ci", "--no-audit"], directory);
  await required("pull-pinned-image", "docker", ["pull", pinnedPlaywrightImage], candidateDirectory);
  await required("warm-container-dependencies", "docker", ["run", "--platform", "linux/arm64", "--rm", "--init",
    "-v", `${baseDirectory}:/workspace`, "-v", "/workspace/node_modules",
    "--mount", "type=volume,source=tempo-playwright-npm-cache,target=/root/.npm", "-w", "/workspace",
    pinnedPlaywrightImage, "bash", "-lc", "npm ci --no-audit"], baseDirectory);
  const performanceDirectories = {};
  for (const [label, directory, commit] of [["before", baseDirectory, baseCommit], ["after", candidateDirectory, candidateCommit]]) {
    const containers = spawnSync("docker", ["ps", "--format", "{{.ID}} {{.Image}}"], { encoding: "utf8" });
    assert.equal(containers.status, 0, containers.stderr);
    assert.equal(containers.stdout.trim(), "", "Unexpected running container before measurement");
    await required(`processes-${label}`, "ps", ["-eo", "pid,ppid,comm,args"], directory);
    const timingDirectory = `test-results/performance/issue-33-${label}`;
    performanceDirectories[label] = join(directory, timingDirectory);
    await run(`perf-${label}`, "make", ["perf"], directory, {
      TEMPO_TEST_TIMING_DIR: timingDirectory, TEMPO_FULL_TEST_RUN_COMMIT: commit,
      TEMPO_CI_REPORT: `test-results/performance/issue-33-${label}/playwright-results.json`,
    });
  }
  for (const [label, directory] of [["before", baseDirectory], ["after", candidateDirectory]]) {
    const unitOutput = join(performanceDirectories[label], "deterministic-workload.json");
    await run(`workload-${label}`, "npm", ["run", "test:unit", "--", "tests/unit/discoveries-tray-regressions.test.tsx",
      "-t", "discovery_synthetic_workload_records_request_and_validation_counts", "--reporter=default", "--reporter=json",
      `--outputFile.json=${join(performanceDirectories[label], "unit-workload.json")}`], directory,
    { TEMPO_DISCOVERY_MEASUREMENT: unitOutput });
  }
  await required("compare-performance", "node", ["scripts/report-performance.mjs", "--directory", performanceDirectories.after,
    "--baseline", performanceDirectories.before], candidateDirectory);
  const reports = Object.fromEntries(Object.entries(performanceDirectories).map(([label, directory]) =>
    [label, JSON.parse(readFileSync(join(directory, "held-drag-chromium.json"), "utf8"))]));
  assert.equal(reports.before.commit, baseCommit); assert.equal(reports.after.commit, candidateCommit);
  assert.deepEqual(reports.before.environment, reports.after.environment);
  assert.deepEqual(reports.before.fixture, reports.after.fixture);
  for (const [label, report] of Object.entries(reports)) {
    assert.equal(report.runs.length, 60, "Incomplete held-drag repetitions");
    assert.equal(report.summaries.length, 10);
    for (const summary of report.summaries) assert.equal(summary.count, 6);
    for (const hold of report.runs) {
      assert.equal(hold.workloadEvidence.error, null);
      if (hold.workload === "discovery-preparation") {
        assert.ok(hold.workloadEvidence.endedAtMs > hold.workloadEvidence.startedAtMs);
      }
      if (hold.workload === "main-thread-stall") assert.ok(hold.probe.samples.some((sample) => sample.gapMs >= 70));
    }
    const unitReport = JSON.parse(readFileSync(join(performanceDirectories[label], "unit-workload.json"), "utf8"));
    assert.equal(unitReport.numPassedTests, 1, "The named workload must actually execute");
    assert.equal(unitReport.numFailedTests, 0);
  }
  evidence.comparison = { matchingEnvironment: true, matchingFixture: true, completeHolds: true,
    heldDragSha256: Object.fromEntries(Object.entries(performanceDirectories).map(([label, directory]) =>
      [label, hash(readFileSync(join(directory, "held-drag-chromium.json")))])) };
  process.exitCode = evidence.commands.some((record) => record.exitCode !== 0) ? 1 : 0;
} catch (error) {
  evidence.error = error.stack; process.exitCode = 1; console.error(error);
} finally {
  save();
}
