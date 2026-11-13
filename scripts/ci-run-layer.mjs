import { verifyStudyDurabilityScenario } from '../database/tests/postgres-test-plan.mjs';
import { spawnSync } from "node:child_process";
import { performance } from "node:perf_hooks";
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, readFileSync, mkdirSync, readdirSync, writeFileSync, mkdtempSync, rmSync, lstatSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { protectRegressionSuite, verificationStages } from "./verification-stages.mjs";
import { escapeRegex, allLayers, planHash } from "./ci-verification-plan.mjs";
import { resolvePython } from "./resolve-python.mjs";
import { postgresTestStages } from "../database/tests/postgres-test-plan.mjs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { cpus, totalmem } from "node:os";
import { aggregateBrowserShards, browserShard, browserShardFingerprint, browserShardReportPath, validateBrowserShardPlan } from "./ci-browser-shards.mjs";
import { withTaskBrowserBuild, isLocalQualificationBuild } from "./task-browser-build.mjs";
import { suiteFingerprint } from "./ci-evidence.mjs";

function postgresVerification(layer, plan) {
  const mode = layer === "postgres" ? "durability" : "lifecycle";
  const planned = plan.jobs[layer];
  if (layer === "postgres") {
    const expected = plan.comparisonAvailable && plan.paths.includes("backend/app/services/queue_presentation.py")
      ? ["study-queue-presentation"] : [];
    if (JSON.stringify(planned?.owner_suites ?? []) !== JSON.stringify(expected))
      throw new Error("PostgreSQL owner obligations differ from affected source");
  }
  if (planned?.mode !== mode || JSON.stringify(planned.planned_stages) !== JSON.stringify(postgresTestStages({ mode }))) {
    throw new Error(`${layer}: PostgreSQL scenario inventory differs from the immutable plan`);
  }
  return planned;
}

export function validateQueueOwnerEvidence(plan, evidence, expectedHashes, expectedMergeBase) {
  const expected = "backend/tests/test_study_encounter_intake_boundary.py::test_applied_game_priority_allows_bounded_daily_queue_preparation_without_losing_attempts";
  const selected = plan.jobs.postgres.owner_suites ?? [];
  if (JSON.stringify(selected) !== JSON.stringify(["study-queue-presentation"])) throw new Error("Unexpected queue owner selection");
  if (!evidence || evidence.suite !== selected[0] || evidence.scope !== "queue-presentation-native-case"
      || evidence.passed !== 1 || evidence.failed !== 0 || evidence.skipped !== 0
      || JSON.stringify(evidence.expectedIdentities) !== JSON.stringify([expected])
      || JSON.stringify(evidence.executedIdentities) !== JSON.stringify([expected])
      || !plan.base || !plan.commit || evidence.comparison?.resolvedHead !== plan.commit
      || evidence.comparison?.resolvedBase !== plan.base || evidence.comparison?.requestedBase !== plan.base
      || JSON.stringify(evidence.comparison) !== JSON.stringify(evidence.comparisonAfter)
      || !expectedMergeBase || evidence.comparison?.mergeBase !== expectedMergeBase || !evidence.python || !evidence.platform
      || !/^tempo-game-feedback-[0-9a-f]{32}$/.test(evidence.fixture?.owner ?? "")
      || !/^[0-9a-f]{64}$/.test(evidence.fixture?.container_id ?? "")
      || !/^sha256:[0-9a-f]{64}$/.test(evidence.fixture?.image_id ?? "")
      || evidence.fixture?.cleanup_verified !== true || !evidence.fixture?.memory_bytes || !evidence.fixture?.pids_limit
      || !Object.keys(evidence.comparison?.sourceInputs ?? {}).length
      || JSON.stringify(evidence.comparison.sourceInputs) !== JSON.stringify(expectedHashes))
    throw new Error("Missing or mismatched queue presentation owner evidence");
}

export function queueOwnerMergeBase(plan) {
  if (!plan.commit || !plan.base) throw new Error("Queue owner proof requires candidate and base");
  const result = spawnSync("git", ["merge-base", plan.commit, plan.base], { encoding: "utf8" });
  if (result.status !== 0 || !result.stdout.trim()) throw new Error("Queue owner merge base unavailable");
  return result.stdout.trim();
}

export function declaredQueueInputHashes(root = process.cwd()) {
  const manifest = JSON.parse(readFileSync(join(root, "components.json"), "utf8"));
  const suite = manifest.suites.find(item => item.id === "study-queue-presentation");
  if (!suite?.inputs?.length) throw new Error("Missing queue owner suite inputs");
  const result = spawnSync("git", ["ls-files", "-z", "--", "components.json", ...suite.inputs], { cwd: root, encoding: "utf8" });
  if (result.status !== 0) throw new Error("Cannot enumerate queue owner inputs");
  const paths = result.stdout.split("\0").filter(Boolean).sort();
  if (!paths.length || ["components.json", ...suite.inputs].some(input => !paths.some(path => path === input || path.startsWith(input.replace(/\/$/, "") + "/"))))
    throw new Error("Missing queue owner input");
  return Object.fromEntries(paths.map(path => [path, createHash("sha256").update(readFileSync(join(root, path))).digest("hex")]));
}

/** Execute exactly the registered owner suite; failed roots remain for inspection. */
export function runQueueOwnerAttempt(plan, run, options = {}) {
  if (plan.hash !== planHash(plan)) throw new Error("Immutable plan hash mismatch");
  const revision = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
  if (revision.status !== 0 || revision.stdout.trim() !== plan.commit) throw new Error("Owner checkout differs from plan candidate");
  postgresVerification("postgres", plan);
  if (!plan.base || !plan.jobs.postgres.owner_suites?.length) throw new Error("Queue owner proof requires its selected suite and base");
  const root = mkdtempSync(join(tmpdir(), "tempo-ci-queue-owner-"));
  mkdirSync(join(root, "fixtures"));
  const environment = { ...process.env, ...(options.environment ?? {}),
    TEMPO_PRIORITY_PROOF_BASE: plan.base, TEMPO_PRIORITY_EVIDENCE_ROOT: root,
    TEMPO_PRIORITY_EVIDENCE: join(root, "evidence.json"), TEMPO_GAME_FEEDBACK_EVIDENCE_ROOT: join(root, "fixtures") };
  try {
    const result = run(environment);
    if (result.status !== 0 || result.error) return { result, failureRoot: root };
    if (lstatSync(environment.TEMPO_PRIORITY_EVIDENCE).isSymbolicLink()) throw new Error("Owner evidence cannot be a symlink");
    const evidence = JSON.parse(readFileSync(environment.TEMPO_PRIORITY_EVIDENCE, "utf8"));
    const fixtureRoot = environment.TEMPO_GAME_FEEDBACK_EVIDENCE_ROOT;
    const reports = readdirSync(fixtureRoot).map(name => join(fixtureRoot, name, "fixture.json")).filter(existsSync);
    if (reports.length !== 1) throw new Error("Expected one fresh owner fixture report");
    if (lstatSync(dirname(reports[0])).isSymbolicLink() || lstatSync(reports[0]).isSymbolicLink())
      throw new Error("Owner fixture evidence cannot be a symlink");
    const fixture = JSON.parse(readFileSync(reports[0], "utf8"));
    const fields = ["owner", "container_id", "image_id", "profile", "storage", "memory_bytes", "pids_limit", "cleanup_verified"];
    if (dirname(reports[0]) !== join(fixtureRoot, fixture.owner ?? "") ||
        fields.some(field => fixture[field] !== evidence.fixture?.[field])) throw new Error("Owner fixture identity differs from proof evidence");
    validateQueueOwnerEvidence(plan, evidence, options.expectedHashes ?? declaredQueueInputHashes(), options.expectedMergeBase ?? queueOwnerMergeBase(plan));
    rmSync(root, { recursive: true, force: true });
    return { result, evidence };
  } catch (error) {
    return { result: { status: 1, error }, failureRoot: root };
  }
}

export function validatePostgresScenarios(layer, plan, scenarios) {
  const planned = postgresVerification(layer, plan);
  if (!scenarios || scenarios.runner !== "postgres" || scenarios.mode !== planned.mode
    || scenarios.commit !== plan.commit || scenarios.plan_hash !== plan.hash
    || JSON.stringify(scenarios.planned_stages) !== JSON.stringify(planned.planned_stages)
    || JSON.stringify(Object.keys(scenarios.stages ?? {}).sort()) !== JSON.stringify([...planned.planned_stages].sort())
    || planned.planned_stages.some(stage => scenarios.stages[stage]?.exit_code !== 0 || scenarios.stages[stage]?.skipped || scenarios.stages[stage]?.retries)) {
    throw new Error(`${layer}: missing, failed or mismatched PostgreSQL scenario results`);
  }
  if (planned.planned_stages.includes("study_durability")) verifyStudyDurabilityScenario(scenarios, planned.mode);
}

export function layerCommands(layer, plan, shardId) {
  if (!allLayers.includes(layer) || !plan.jobs[layer]?.applicable) throw new Error(`Layer ${layer} is not applicable in the captured plan`);
  if (layer === "browser") {
    validateBrowserShardPlan(plan);
    if (shardId !== undefined) return browserShard(plan, shardId).commands;
    if (plan.browserShards?.count > 1) return [["browser_aggregate", "node", ["scripts/ci-aggregate-browser.mjs"]]];
  }
  const tier = `ci-${layer}`;
  const { stages, stagesByTier } = verificationStages({ python: resolvePython(), tier, outputDirectory: "test-results/performance" });
  if (stagesByTier[tier]) {
    let commands = stages.filter(([name]) => stagesByTier[tier].includes(name));
    if (plan.tier === "development" && ["frontend", "backend"].includes(layer) && plan.core[layer] !== "all") {
      const files = plan.core[layer];
      if (!files?.length) throw new Error("Selected core collection cannot be empty");
      commands = commands.map(([name, command, args]) =>
        [name, command, name === "unit" ? [...args, ...files] : name === "backend" ? args.flatMap(argument => argument === "backend/tests" ? files : [argument]) : args]);
    }
    if (layer === "frontend") {
      commands = commands.map(([name, command, args]) => [name, command, name === "unit" ? [...args, "--includeTaskLocation"] : args]);
      const files = Array.isArray(plan.core.frontend) ? plan.core.frontend : [];
      commands.unshift(["unit_inventory", "npx", ["vitest", "list", ...files, "--no-static-parse", "--includeTaskLocation", "--json=test-results/ci/frontend-inventory.json"]]);
    } else if (layer === "backend") {
      const backendArguments = commands.find(([name]) => name === "backend")[2].filter(argument => !argument.startsWith("--junitxml="));
      commands.unshift(["backend_inventory", resolvePython(), ["scripts/ci-collect-backend.py", "test-results/ci/backend-inventory.json", ...backendArguments.slice(2)]]);
    }
    return commands;
  }
  if (["postgres", "lifecycle"].includes(layer)) {
    const { mode } = postgresVerification(layer, plan);
    const commands = [["capabilities", "node", ["scripts/check-test-capabilities.mjs", "--docker", "--loopback", "--workspace-mount"]],
      [mode, "node", ["database/tests/test-postgres-docker.mjs", "--mode", mode]]];
    if (layer === "postgres" && plan.jobs.postgres.owner_suites?.includes("study-queue-presentation"))
      commands.push(["study-queue-presentation", "node", ["tools/component-build/test-lane.mjs", "--suite", "study-queue-presentation"]]);
    return commands;
  }
  if (layer === "visual") return [["capabilities", "node", ["scripts/check-test-capabilities.mjs", "--docker", "--workspace-mount"]], ["visual", "npm", ["run", "test:visual"]]];
  const grep = layer === "quarantine" ? plan.collection.filter(item => item.quarantined).map(item => item.grep ?? `^${escapeRegex(item.fullTitle)}$`).join("|") : plan.browserGrep;
  if (!grep) throw new Error("Selected browser collection cannot be empty");
  const focus = layer === "browser" && plan.collection.every(item => item.selected) ? [] : ["--browser-grep", grep];
  return [["capabilities", "node", ["scripts/check-test-capabilities.mjs", "--docker", "--loopback", "--workspace-mount"]],
    ["browser", "node", ["database/tests/test-postgres-docker.mjs", "--mode", "browser", ...focus]]];
}

export function browserResults(report) {
  const tests = [];
  function visit(suite) {
    for (const spec of suite.specs ?? []) for (const test of spec.tests ?? []) {
      const results = test.results ?? [];
      tests.push({ id: `${spec.id}:${test.projectName}`, title: spec.title, project: test.projectName,
        status: results.length === 1 && test.status === "expected" ? results[0].status : "failed", retries: Math.max(0, results.length - 1),
        duration_ms: results.reduce((duration, result) => duration + result.duration, 0) });
    }
    for (const child of suite.suites ?? []) visit(child);
  }
  for (const suite of report.suites ?? []) visit(suite);
  if (report.errors?.length) throw new Error("Browser reporter contains collection/runtime errors");
  return tests;
}

export function validateUnitResults(layer, plan, report) {
  if (!report?.test_count || report.failed || report.skipped) throw new Error(`${layer}: missing, failed, skipped or zero-test results`);
  const expected = report.expectedTests, observed = report.tests;
  if (!expected?.length || !observed?.length || report.test_count !== observed.length
    || JSON.stringify(expected.map(test => test.id).sort()) !== JSON.stringify(observed.map(test => test.id).sort())
    || observed.some(test => test.status !== "passed" || test.retries !== 0)) throw new Error(`${layer}: executed tests differ from the collected inventory`);
  const selected = plan.core?.[layer];
  const requiredFiles = [...new Set([...(Array.isArray(selected) ? selected : []), ...(plan.regressionFiles?.[layer] ?? [])])];
  for (const file of requiredFiles) {
    if (!(report.files?.[file] > 0)) throw new Error(`${layer}: changed regression file did not execute: ${file}`);
  }
}

function frontendTestIdentity(file, name, location) {
  const relativeFile = file.replaceAll("\\", "/").split(`${process.cwd()}/`).at(-1);
  if (!location?.line || !location?.column) throw new Error("Frontend inventory/results lack test locations");
  return { file: relativeFile, id: `${relativeFile}:${location.line}:${location.column}::${name}` };
}

export function frontendInventory(entries) {
  return entries.map(test => frontendTestIdentity(test.file, test.name, test.location));
}

export function frontendResults(unit) {
  return (unit.testResults ?? []).flatMap(file => (file.assertionResults ?? []).map(test => ({
    ...frontendTestIdentity(file.name, [...test.ancestorTitles, test.title].join(" > "), test.location),
    status: test.status, retries: test.meta?.retryCount ?? 0 })));
}

export function backendUnitFileCounts(xml, requiredFiles) {
  return Object.fromEntries(requiredFiles.map(file => {
    const moduleName = file.slice(0, -3).replaceAll("/", ".");
    return [file, [...xml.matchAll(/<testcase\b[^>]*>/g)]
      .filter(match => {
        const reportedClass = match[0].match(/\sclassname="([^"]*)"/)?.[1];
        return reportedClass === moduleName || reportedClass?.startsWith(`${moduleName}.`);
      }).length];
  }));
}

// Dependency injection proves diagnostic repeats cannot overwrite the first result.
export function executeLayer(commands, run, diagnosticRetry = false) {
  const results = [], diagnostics = [];
  for (const [name, command, args] of commands) {
    const start = performance.now();
    const first = run(command, args);
    results.push({ name, command, args, exit_code: first.status, error: first.error?.message ?? null,
      duration_seconds: Math.round((performance.now() - start) / 10) / 100 });
    if (first.status !== 0 || first.error) {
      if (diagnosticRetry) { const retry = run(command, args, true); diagnostics.push({ name, exit_code: retry.status, error: retry.error?.message ?? null }); }
      break;
    }
  }
  return { commands: results, diagnostics, status: results.length === commands.length && results.every(result => result.exit_code === 0 && !result.error) ? "success" : "failed" };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const layer = process.argv[2];
  const shardArgument = process.argv.indexOf("--shard");
  const shardId = shardArgument < 0 ? undefined : Number(process.argv[shardArgument + 1]);
  if (shardId !== undefined && layer !== "browser") throw new Error("Only browser qualification can execute shards");
  const plan = JSON.parse(readFileSync("test-results/ci/plan.json", "utf8"));
  mkdirSync("test-results/ci", { recursive: true }); mkdirSync("test-results/performance", { recursive: true });
  const reportPath = shardId === undefined ? `test-results/ci/${layer}.json` : browserShardReportPath(shardId);
  mkdirSync(dirname(reportPath), { recursive: true });
  const report = { version: 2, layer, planHash: plan.hash, commit: plan.commit,
    executionKey: shardId === undefined ? suiteFingerprint(plan, layer, layerCommands(layer, plan)) : browserShardFingerprint(plan, shardId),
    ...(shardId === undefined ? {} : { shardId, assignmentHash: plan.browserShards.hash }),
    execution: { kind: "executed", runId: Number(process.env.GITHUB_RUN_ID ?? 0), attempt: Number(process.env.GITHUB_RUN_ATTEMPT ?? 0) },
    completed: false, status: "failed", commands: [], tests: [],
    timestamp: new Date().toISOString(), environment: { node: process.version, platform: process.platform, architecture: process.arch,
      python: spawnSync(resolvePython(), ["--version"], { encoding: "utf8" }).stdout?.trim() ?? null,
      rust: spawnSync("rustc", ["--version"], { encoding: "utf8" }).stdout?.trim() ?? null,
      cpuCount: cpus().length, memoryBytes: totalmem(),
      wasmPack: spawnSync("wasm-pack", ["--version"], { encoding: "utf8" }).stdout?.trim() ?? null } };
  try {
    const revision = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
    if (plan.hash !== planHash(plan)) throw new Error("Immutable plan hash mismatch");
    if (revision.stdout.trim() !== plan.commit) throw new Error("Layer checkout differs from the immutable plan revision");
    protectRegressionSuite("tests"); protectRegressionSuite("backend/tests");
    const commands = layerCommands(layer, plan, shardId);
    const testReportPath = shardId === undefined ? `test-results/ci/${layer}-tests.json` : `${dirname(reportPath)}/tests.json`;
    const env = { ...process.env, PYTHONPATH: "backend:database", TEMPO_CI_PLAN_HASH: plan.hash, TEMPO_CI_REPORT: testReportPath };
    Object.assign(report, executeLayer(commands, (command, args, diagnostic) => {
      if (diagnostic) for (const path of [env.TEMPO_CI_REPORT, "test-results/ci/backend-tests.xml", "test-results/performance/unit-files-ci-frontend.json", "test-results/ci/frontend-inventory.json", "test-results/ci/backend-inventory.json"]) {
        if (existsSync(path)) copyFileSync(path, `${path}.first-failure`);
      }
      const environment = diagnostic ? { ...env, TEMPO_CI_REPORT: `${testReportPath}.diagnostic` } : { ...env };
      const run = selectedEnv => spawnSync(command, args, { stdio: "inherit", env: selectedEnv });
      if (layer === "postgres" && args.includes("study-queue-presentation")) {
        const attempt = runQueueOwnerAttempt(plan, run, { environment });
        if (attempt.failureRoot) (report.ownerAttemptFailures ??= []).push({ diagnostic: Boolean(diagnostic), root: attempt.failureRoot });
        if (!diagnostic && attempt.evidence) report.ownerEvidence = { planHash: plan.hash, head: plan.head, evidence: attempt.evidence };
        return attempt.result;
      }
      return isLocalQualificationBuild(command, args)
        ? withTaskBrowserBuild(output => run({ ...environment, TEMPO_BROWSER_BUILD_DIR: output }))
        : run(environment);
    }, process.env.TEMPO_DIAGNOSTIC_RETRY === "true"));
    if (["browser", "visual", "quarantine"].includes(layer)) {
      if (layer === "browser" && shardId === undefined && plan.browserShards?.count > 1) {
        const aggregate = JSON.parse(readFileSync("test-results/ci/browser-aggregate.json", "utf8"));
        report.shards = aggregate.shards; report.shardJobs = aggregate.shardJobs;
        report.slowestRunnerSeconds = aggregate.slowestRunnerSeconds;
        report.tests = aggregateBrowserShards(plan, report.shards, report.execution);
      } else {
        const original = existsSync(`${testReportPath}.first-failure`) ? `${testReportPath}.first-failure` : testReportPath;
        report.tests = browserResults(JSON.parse(readFileSync(original, "utf8")));
      }
      if (layer === "browser") {
        const expected = shardId === undefined ? plan.collection.filter(item => item.selected).map(item => item.id).sort() : browserShard(plan, shardId).testIds;
        if (JSON.stringify(expected) !== JSON.stringify(report.tests.map(item => item.id).sort()) || !expected.length) throw new Error("Executed browser collection differs from plan");
      }
      if (!report.tests.length || report.tests.some(test => test.status !== "passed" || test.retries)) report.status = "failed";
    } else if (layer === "frontend") {
      const originalPath = path => existsSync(`${path}.first-failure`) ? `${path}.first-failure` : path;
      const unit = JSON.parse(readFileSync(originalPath("test-results/performance/unit-files-ci-frontend.json"), "utf8"));
      if (!unit.numTotalTests || unit.numFailedTests || unit.numPendingTests) throw new Error("Missing, failed or skipped frontend unit results");
      report.test_count = unit.numTotalTests;
      report.expectedTests = frontendInventory(JSON.parse(readFileSync(originalPath("test-results/ci/frontend-inventory.json"), "utf8")));
      report.tests = frontendResults(unit);
      report.files = Object.fromEntries((unit.testResults ?? []).map(file => [file.name.replaceAll("\\", "/").split(`${process.cwd()}/`).at(-1), file.assertionResults?.length ?? 0]));
      validateUnitResults(layer, plan, report);
    } else if (layer === "backend") {
      if (!existsSync("test-results/ci/backend-tests.xml")) throw new Error("Backend JUnit results missing");
      const originalPath = path => existsSync(`${path}.first-failure`) ? `${path}.first-failure` : path;
      const xmlPath = originalPath("test-results/ci/backend-tests.xml");
      const suites = [...readFileSync(xmlPath, "utf8").matchAll(/<testsuite\b[^>]*>/g)].map(match => match[0]);
      report.test_count = suites.reduce((count, suite) => count + Number(suite.match(/\btests="(\d+)"/)?.[1] ?? 0), 0);
      if (!report.test_count || suites.some(suite => ["failures", "errors", "skipped"].some(attribute => Number(suite.match(new RegExp(`\\b${attribute}="(\\d+)"`))?.[1] ?? 0)))) throw new Error("Missing, failed or skipped backend results");
      const xml = readFileSync(xmlPath, "utf8");
      const parsed = spawnSync(resolvePython(), ["scripts/ci-collect-backend.py", "--results", xmlPath], { encoding: "utf8", maxBuffer: 8 * 1024 * 1024 });
      if (parsed.status !== 0) throw new Error("Backend test identities could not be read");
      report.tests = JSON.parse(parsed.stdout);
      report.expectedTests = JSON.parse(readFileSync(originalPath("test-results/ci/backend-inventory.json"), "utf8"));
      report.files = backendUnitFileCounts(xml, [...new Set([...(Array.isArray(plan.core.backend) ? plan.core.backend : []), ...(plan.regressionFiles?.backend ?? [])])]);
      validateUnitResults(layer, plan, report);
    } else if (["postgres", "lifecycle"].includes(layer)) {
      const { mode } = postgresVerification(layer, plan);
      const files = readdirSync("test-results/performance").filter(file => file.startsWith(`postgres-scenarios-${mode}-`));
      if (files.length !== 1) throw new Error("Missing or ambiguous PostgreSQL scenario report");
      const scenarios = JSON.parse(readFileSync(`test-results/performance/${files[0]}`, "utf8"));
      validatePostgresScenarios(layer, plan, scenarios);
      report.scenarios = scenarios;
    }
    report.completed = true;
  } catch (error) { report.error = error.message; report.status = "failed"; console.error(error); }
  finally { writeFileSync(reportPath, JSON.stringify(report, null, 2)); }
  process.exitCode = report.status === "success" ? 0 : 1;
}
