import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { inventory, mandatoryLayers, allLayers, changedPathsFromNameStatus, validateInventory, verificationPlan, collectCases } from "../../scripts/ci-verification-plan.mjs";
import { evaluateQuality, deploymentAllowed } from "../../scripts/ci-quality.mjs";
import { executeLayer, layerCommands, browserResults } from "../../scripts/ci-run-layer.mjs";
import { postgresTestStages } from "../../scripts/postgres-test-plan.mjs";

const files = Object.values(inventory.families).flat();
const cases = files.filter(file => !inventory.families.pinned.includes(file)).flatMap(file => [
  { id: `${file}:ordinary`, file, title: "ordinary case", fullTitle: `chromium ${file} ordinary case`, project: "chromium" },
  ...inventory.critical.filter(item => item.file === file).map(item => ({ id: `${file}:${item.title}`, file, title: item.title, fullTitle: `chromium ${file} ${item.title}`, project: "chromium" })),
]);
function plan(options = {}) { return { ...verificationPlan({ paths: ["docs/testing.md"], files, cases, pinnedCases: [{ id: "visual", file: "visual.spec.ts", title: "visual" }], ...options }), commit: "revision" }; }
function successfulResults(planned) {
  const needs = { plan: { result: "success" } }, reports = {};
  for (const layer of allLayers) {
    needs[layer] = { result: planned.jobs[layer].applicable ? "success" : "skipped" };
    if (!planned.jobs[layer].applicable) continue;
    reports[layer] = { layer, commit: planned.commit, planHash: planned.hash, completed: true, status: "success", test_count: 123,
      commands: layerCommands(layer, planned).map(([name]) => ({ name, exit_code: 0 })),
      tests: (layer === "visual" ? [{ id: "visual" }] : planned.collection.filter(item => layer === "quarantine" ? item.quarantined : item.selected)).map(item => ({ id: item.id, status: "passed", retries: 0 })),
      scenarios: { planned_stages: postgresTestStages({ mode: "durability" }), stages: Object.fromEntries(postgresTestStages({ mode: "durability" }).map(name => [name, { exit_code: 0 }])) } };
  }
  return { needs, reports };
}

test("core and required integration failures block quality", () => {
  const planned = plan();
  for (const layer of mandatoryLayers) {
    const { needs, reports } = successfulResults(planned);
    needs[layer].result = "failure"; reports[layer].status = "failed";
    assert.equal(evaluateQuality(planned, needs, reports).success, false, layer);
  }
});

test("missing cancelled and unexpectedly skipped required work cannot pass quality", () => {
  const planned = plan();
  for (const layer of ["plan", ...mandatoryLayers]) for (const result of [undefined, "cancelled", "skipped"]) {
    const { needs, reports } = successfulResults(planned);
    needs[layer].result = result;
    assert.equal(evaluateQuality(planned, needs, reports).success, false, `${layer}: ${result}`);
  }
  for (const layer of mandatoryLayers) {
    const { needs, reports } = successfulResults(planned); delete reports[layer];
    assert.equal(evaluateQuality(planned, needs, reports).success, false);
  }
});

test("absent required command and selected test results cannot pass quality", () => {
  const planned = plan({ complete: true });
  for (const layer of mandatoryLayers) {
    const { needs, reports } = successfulResults(planned); reports[layer].commands.pop();
    assert.equal(evaluateQuality(planned, needs, reports).success, false);
  }
  for (const missing of ["tests", "scenarios"]) {
    const { needs, reports } = successfulResults(planned);
    delete reports[missing === "tests" ? "browser" : "postgres"][missing];
    assert.equal(evaluateQuality(planned, needs, reports).success, false);
  }
  const { needs, reports } = successfulResults(planned); reports.browser.tests[0].status = "skipped";
  assert.equal(evaluateQuality(planned, needs, reports).success, false);
});

test("explicit inapplicability is accepted and reported but unexpected skips fail", () => {
  const planned = plan(); const { needs, reports } = successfulResults(planned);
  assert.equal(planned.jobs.visual.applicable, false);
  assert.equal(evaluateQuality(planned, needs, reports).success, true);
  assert(planned.jobs.visual.reason.includes("No rendering"));
  const full = plan({ complete: true }); const results = successfulResults(full); results.needs.visual.result = "skipped";
  assert.equal(evaluateQuality(full, results.needs, results.reports).success, false);
});

test("quarantined harness failures remain visible with required replacement coverage", () => {
  const quarantine = [{ id: cases[0].id, confirmedHarnessDefect: true, issue: "https://github.com/example/repo/issues/1", owner: "owner", expires: "2099-01-01", requiredCoverage: [cases[1].id] }];
  const planned = plan({ quarantine }); const { needs, reports } = successfulResults(planned);
  needs.quarantine.result = "failure"; reports.quarantine.status = "failed";
  const verdict = evaluateQuality(planned, needs, reports);
  assert.equal(verdict.success, true); assert.equal(verdict.nonblocking.length, 1);
  assert(planned.collection.find(item => item.id === cases[1].id).selected);
  assert.equal(planned.collection.find(item => item.id === cases[0].id).selected, false);
  for (const result of ["cancelled", "skipped", undefined]) {
    needs.quarantine.result = result;
    assert.equal(evaluateQuality(planned, needs, reports).success, false);
  }
  for (const invalid of [{ ...quarantine[0], expires: "2000-01-01" }, { ...quarantine[0], owner: "" }, { ...quarantine[0], confirmedHarnessDefect: false }]) assert.throws(() => plan({ quarantine: [invalid] }), /Quarantine/);
  assert.deepEqual(JSON.parse(readFileSync("scripts/ci-quarantine.json", "utf8")), []);
});

test("rename deletion unknown paths and missing history select conservative coverage", () => {
  const paths = changedPathsFromNameStatus("R100\0app/components/discoveries-tray.tsx\0backend/app/main.py\0D\0backend/migrations/021.sql\0");
  assert(paths.includes("app/components/discoveries-tray.tsx")); assert(paths.includes("backend/app/main.py")); assert(paths.includes("backend/migrations/021.sql"));
  for (const options of [{ paths }, { paths: ["unknown/new-file.txt"] }, { paths: ["unknown/new-file.md"] }, { comparisonAvailable: false }, { paths: ["package-lock.json"] }, { paths: ["app/components/chessboard.tsx"] }]) {
    const planned = plan(options); assert.equal(planned.scope, "complete"); assert.equal(planned.collection.filter(item => item.selected).length, cases.length);
  }
  assert.throws(() => changedPathsFromNameStatus("R100\0one\0"), /Invalid/);
  assert.throws(() => changedPathsFromNameStatus("U\0app/components/discoveries-tray.tsx\0"), /Uncertain/);
});

test("new unclassified specs fail planning and every test has nightly and release coverage", () => {
  assert.throws(() => validateInventory([...files, "new-feature.spec.ts"]), /Unclassified/);
  validateInventory(readdirSync("tests/browser").filter(file => file.endsWith(".spec.ts")));
  assert(plan().collection.every(item => item.nightly && item.release));
  assert(plan().collection.filter(item => item.critical).every(item => item.selected));
});

test("leaf source selection includes complete families and rendering selects pinned checks", () => {
  const planned = plan({ paths: ["app/components/discoveries-tray.tsx"] });
  assert.equal(planned.scope, "targeted"); assert(planned.jobs.visual.required);
  assert(planned.collection.filter(item => inventory.families.discoveries.includes(item.file)).every(item => item.selected));
  assert.equal(plan({ paths: ["app/domain/opening-segmentation.ts"] }).jobs.visual.applicable, false);
});

test("split complete coverage preserves every PostgreSQL product scenario", () => {
  const full = postgresTestStages({ mode: "full" });
  const split = new Set([...postgresTestStages({ mode: "durability" }), ...postgresTestStages({ mode: "browser" })]);
  assert.deepEqual([...split].sort(), full.filter(stage => stage !== "study_isolation").sort());
  // study_isolation destroys the old browser volume; the split owners instead
  // each create fresh ports, credentials and volumes in the existing runner.
  const runner = readFileSync("scripts/test-postgres-docker.mjs", "utf8");
  assert(runner.includes('randomBytes(4).toString("hex")')); assert(runner.includes("verifyProjectIsUnused"));
});

test("deployment requires complete verification and scheduled or verification-only runs cannot deploy", () => {
  const common = { event: "push", ref: "refs/heads/main", scope: "complete", quality: "success" };
  assert(deploymentAllowed(common));
  for (const override of [{ event: "schedule" }, { event: "pull_request" }, { event: "merge_group" }, { scope: "targeted" }, { quality: "failure" }, { ref: "refs/heads/feature" }, { event: "workflow_dispatch", verificationOnly: true }]) assert.equal(deploymentAllowed({ ...common, ...override }), false);
  assert(deploymentAllowed({ ...common, event: "workflow_dispatch", verificationOnly: false }));
  const workflow = readFileSync(".github/workflows/pages.yml", "utf8");
  assert(workflow.includes("cron: '0 7 * * *'")); assert(workflow.includes("if: always()\n    needs: [plan,"));
  assert.equal(workflow.split("inputs.verification_only == false").length - 1, 2);
  for (const layer of mandatoryLayers) assert(workflow.includes(`  ${layer}:\n    needs: plan\n    uses: ./.github/workflows/verify-layer.yml`));
});

test("failed-layer rerun leaves successful unrelated jobs intact and diagnostic retry cannot green the gate", () => {
  const commands = [["frontend", "npm", []], ["backend", "python", []]];
  let calls = 0;
  const defaultRun = executeLayer(commands, () => { calls++; return { status: 1 }; });
  assert.equal(calls, 1); assert.equal(defaultRun.status, "failed");
  const diagnostic = executeLayer(commands.slice(0, 1), () => ({ status: ++calls === 2 ? 1 : 0 }), true);
  assert.equal(diagnostic.status, "failed"); assert.equal(diagnostic.commands[0].exit_code, 1); assert.equal(diagnostic.diagnostics[0].exit_code, 0);
  // Independent workflow_call jobs need only the plan, never another layer.
  const workflow = readFileSync(".github/workflows/pages.yml", "utf8");
  assert(!/needs: \[(?:frontend|backend|build|postgres|browser)/.test(workflow));
});

test("actual browser collection grep selects exactly the planned tests", () => {
  const environment = { ...process.env, TEMPO_DOCKER_URL: "http://127.0.0.1:1" };
  function collect(grep) {
    const result = spawnSync("npx", ["playwright", "test", "--list", "--reporter=json", ...(grep ? ["--grep", grep] : [])], { encoding: "utf8", env: environment, maxBuffer: 20 * 1024 * 1024 });
    assert.equal(result.status, 0, result.stderr); return collectCases(JSON.parse(result.stdout));
  }
  const realCases = collect();
  const planned = verificationPlan({ paths: ["docs/testing.md"], files: readdirSync("tests/browser").filter(file => file.endsWith(".spec.ts")), cases: realCases });
  assert.deepEqual(collect(planned.browserGrep).map(item => item.id).sort(), planned.collection.filter(item => item.selected).map(item => item.id).sort());
  assert.equal(planned.collection.filter(item => item.selected).length, 7);
  const results = browserResults({ suites: [{ specs: [{ id: "case", title: "title", tests: [{ projectName: "chromium", status: "skipped", results: [{ status: "skipped", duration: 0 }] }] }] }] });
  assert.equal(results[0].status, "failed");
});


test("diagnostic artifact failure still cleans owned resources and preserves both failures", async () => {
  const { executeDiagnosticCleanup } = await import("../../scripts/postgres-test-plan.mjs");
  const captureFailure = new Error("artifact write denied"), cleanupFailure = new Error("cleanup denied");
  let cleaned = false;
  await assert.rejects(executeDiagnosticCleanup(() => { throw captureFailure; }, () => { cleaned = true; }), error => error === captureFailure);
  assert(cleaned);
  await assert.rejects(executeDiagnosticCleanup(() => { throw captureFailure; }, () => { throw cleanupFailure; }), error => error instanceof AggregateError && error.errors[0] === captureFailure && error.errors[1] === cleanupFailure);
});


test("CI and local runners preserve rejection of exclusive skipped and unfinished regressions", async () => {
  const { protectRegressionSuite } = await import("../../scripts/verification-stages.mjs");
  const { mkdtempSync, writeFileSync, rmSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const directory = mkdtempSync(join(tmpdir(), "tempo-ci-guard-"));
  try {
    for (const modifier of ["skip", "only", "todo"]) {
      writeFileSync(join(directory, "fixture.test.ts"), `it.${modifier}("excluded", () => {});`);
      assert.throws(() => protectRegressionSuite(directory), /cannot contain/);
    }
  } finally { rmSync(directory, { recursive: true, force: true }); }
  for (const file of ["scripts/test-all.mjs", "scripts/ci-run-layer.mjs", "scripts/ci-verification-plan.mjs"]) assert(readFileSync(file, "utf8").includes('protectRegressionSuite("tests")'));
});

test("collected browser filenames cannot bypass inventory through extensions or nested paths", () => {
  for (const file of ["new-case.test.ts", "new-case.spec.tsx", "nested/discovery-viewer.spec.ts"]) {
    assert.throws(() => plan({ cases: [...cases, { id: file, file, title: "unclassified", fullTitle: file, project: "chromium" }] }), /Unclassified/);
  }
});


test("complete browser verification is unfiltered and tagged collection preserves selection", () => {
  const full = plan({ complete: true });
  assert(!layerCommands("browser", full).at(-1)[2].includes("--browser-grep"));
  const tagged = collectCases({ suites: [{ title: "board-state.spec.ts", suites: [{ title: "orientation", specs: [{ id: "tagged", file: "board-state.spec.ts", title: "tagged case", tags: ["@orientation", "@critical"], tests: [{ projectName: "chromium" }] }] }] }] });
  const expression = new RegExp(tagged[0].grep);
  assert(expression.test("chromium board-state.spec.ts orientation @orientation tagged case @critical"));
  assert(!expression.test("chromium another.spec.ts orientation @orientation tagged case @critical"));
});
