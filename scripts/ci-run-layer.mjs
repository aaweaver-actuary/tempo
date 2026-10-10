import { spawnSync } from "node:child_process";
import { performance } from "node:perf_hooks";
import { copyFileSync, existsSync, readFileSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { protectRegressionSuite, verificationStages } from "./verification-stages.mjs";
import { escapeRegex, allLayers, planHash } from "./ci-verification-plan.mjs";
import { resolvePython } from "./resolve-python.mjs";
import { postgresTestStages } from "./postgres-test-plan.mjs";
import { suiteFingerprint } from "./ci-evidence.mjs";

function postgresVerification(layer, plan) {
  const mode = layer === "postgres" ? "durability" : "lifecycle";
  const planned = plan.jobs[layer];
  if (planned?.mode !== mode || JSON.stringify(planned.planned_stages) !== JSON.stringify(postgresTestStages({ mode }))) {
    throw new Error(`${layer}: PostgreSQL scenario inventory differs from the immutable plan`);
  }
  return planned;
}

export function validatePostgresScenarios(layer, plan, scenarios) {
  const planned = postgresVerification(layer, plan);
  if (!scenarios || scenarios.runner !== "postgres" || scenarios.mode !== planned.mode
    || scenarios.commit !== plan.commit || scenarios.plan_hash !== plan.hash
    || JSON.stringify(scenarios.planned_stages) !== JSON.stringify(planned.planned_stages)
    || JSON.stringify(Object.keys(scenarios.stages ?? {}).sort()) !== JSON.stringify([...planned.planned_stages].sort())
    || planned.planned_stages.some(stage => scenarios.stages[stage]?.exit_code !== 0)) {
    throw new Error(`${layer}: missing, failed or mismatched PostgreSQL scenario results`);
  }
}

export function layerCommands(layer, plan) {
  if (!allLayers.includes(layer) || !plan.jobs[layer]?.applicable) throw new Error(`Layer ${layer} is not applicable in the captured plan`);
  const tier = `ci-${layer}`;
  const { stages, stagesByTier } = verificationStages({ python: resolvePython(), tier, outputDirectory: "test-results/performance" });
  if (stagesByTier[tier]) {
    let commands = stages.filter(([name]) => stagesByTier[tier].includes(name));
    if (plan.tier === "development" && ["frontend", "backend"].includes(layer) && plan.core[layer] !== "all") {
      const files = plan.core[layer];
      if (!files?.length) throw new Error("Selected core collection cannot be empty");
      commands = commands.filter(([name]) => name !== "defense_engine").map(([name, command, args]) =>
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
    return [["capabilities", "node", ["scripts/check-test-capabilities.mjs", "--docker", "--loopback", "--workspace-mount"]],
      [mode, "node", ["scripts/test-postgres-docker.mjs", "--mode", mode]]];
  }
  if (layer === "visual") return [["capabilities", "node", ["scripts/check-test-capabilities.mjs", "--docker", "--workspace-mount"]], ["visual", "npm", ["run", "test:visual"]]];
  const grep = layer === "quarantine" ? plan.collection.filter(item => item.quarantined).map(item => item.grep ?? `^${escapeRegex(item.fullTitle)}$`).join("|") : plan.browserGrep;
  if (!grep) throw new Error("Selected browser collection cannot be empty");
  const focus = layer === "browser" && plan.collection.every(item => item.selected) ? [] : ["--browser-grep", grep];
  return [["capabilities", "node", ["scripts/check-test-capabilities.mjs", "--docker", "--loopback", "--workspace-mount"]],
    ["browser", "node", ["scripts/test-postgres-docker.mjs", "--mode", "browser", ...focus]]];
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
  const plan = JSON.parse(readFileSync("test-results/ci/plan.json", "utf8"));
  mkdirSync("test-results/ci", { recursive: true }); mkdirSync("test-results/performance", { recursive: true });
  const report = { version: 2, layer, planHash: plan.hash, commit: plan.commit,
    executionKey: suiteFingerprint(plan, layer, layerCommands(layer, plan)),
    execution: { kind: "executed", runId: Number(process.env.GITHUB_RUN_ID ?? 0), attempt: Number(process.env.GITHUB_RUN_ATTEMPT ?? 0) },
    completed: false, status: "failed", commands: [], tests: [],
    timestamp: new Date().toISOString(), environment: { node: process.version, platform: process.platform, architecture: process.arch,
      python: spawnSync(resolvePython(), ["--version"], { encoding: "utf8" }).stdout?.trim() ?? null,
      rust: spawnSync("rustc", ["--version"], { encoding: "utf8" }).stdout?.trim() ?? null,
      wasmPack: spawnSync("wasm-pack", ["--version"], { encoding: "utf8" }).stdout?.trim() ?? null } };
  try {
    const revision = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
    if (plan.hash !== planHash(plan)) throw new Error("Immutable plan hash mismatch");
    if (revision.stdout.trim() !== plan.commit) throw new Error("Layer checkout differs from the immutable plan revision");
    protectRegressionSuite("tests"); protectRegressionSuite("backend/tests");
    const commands = layerCommands(layer, plan);
    const env = { ...process.env, PYTHONPATH: "backend", TEMPO_CI_PLAN_HASH: plan.hash, TEMPO_CI_REPORT: `test-results/ci/${layer}-tests.json` };
    Object.assign(report, executeLayer(commands, (command, args, diagnostic) => {
      if (diagnostic) for (const path of [env.TEMPO_CI_REPORT, "test-results/ci/backend-tests.xml", "test-results/performance/unit-files-ci-frontend.json", "test-results/ci/frontend-inventory.json", "test-results/ci/backend-inventory.json"]) {
        if (existsSync(path)) copyFileSync(path, `${path}.first-failure`);
      }
      return spawnSync(command, args, { stdio: "inherit", env: diagnostic ? { ...env, TEMPO_CI_REPORT: `test-results/ci/${layer}-diagnostic-tests.json` } : env });
    }, process.env.TEMPO_DIAGNOSTIC_RETRY === "true"));
    if (["browser", "visual", "quarantine"].includes(layer)) {
      report.tests = browserResults(JSON.parse(readFileSync(env.TEMPO_CI_REPORT, "utf8")));
      if (layer === "browser") {
        const expected = plan.collection.filter(item => item.selected).map(item => item.id).sort();
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
  finally { writeFileSync(`test-results/ci/${layer}.json`, JSON.stringify(report, null, 2)); }
  process.exitCode = report.status === "success" ? 0 : 1;
}
