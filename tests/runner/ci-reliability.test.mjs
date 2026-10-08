import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { inventory, mandatoryLayers, allLayers, changedPathsFromNameStatus, validateInventory, verificationPlan, collectCases } from "../../scripts/ci-verification-plan.mjs";
import { evaluateQuality, deploymentAllowed } from "../../scripts/ci-quality.mjs";
import { executeLayer, layerCommands, browserResults } from "../../scripts/ci-run-layer.mjs";
import { postgresTestStages } from "../../scripts/postgres-test-plan.mjs";

const demotedCriticalCases = [
  { file: "opening-evidence.spec.ts", family: "training", title: "AS-15 a real tab lease releases stranded evidence into a later idle slice" },
  { file: "opening-evidence.spec.ts", family: "training", title: "AS-08 deferred evidence persistence leaves rendered moves and aggregate review responsive" },
  { file: "opening-evidence.spec.ts", family: "training", title: "AS-15 recovered evidence waits for foreground queue readiness and an idle opportunity" },
  { file: "opening-evidence.spec.ts", family: "training", title: "AS-16 restarted opening board records guided arrows and retains the prior partial attempt" },
  { file: "opening-evidence.spec.ts", family: "training", title: "AS-16 local review quota saves the aggregate and retains evidence through a late checkpoint receipt" },
  { file: "opening-evidence.spec.ts", family: "training", title: "AS-16 offline compact quota failure blocks advancement until durable retry" },
  { file: "opening-evidence.spec.ts", family: "training", title: "AS-16 offline evidence quota saves a compact phone review and retains its journal after sync" },
  { file: "studies.spec.ts", family: "studies", title: "prepared study response is graded offline and replayed with its actual squares" },
];

let completeBrowserCases;
function collectBrowserCases(grep) {
  const result = spawnSync("npx", ["playwright", "test", "--list", "--reporter=json", ...(grep ? ["--grep", grep] : [])], {
    encoding: "utf8", env: { ...process.env, TEMPO_DOCKER_URL: "http://127.0.0.1:1" }, maxBuffer: 20 * 1024 * 1024,
  });
  assert.equal(result.status, 0, result.stderr);
  return collectCases(JSON.parse(result.stdout));
}
function currentBrowserCases() { return completeBrowserCases ??= collectBrowserCases(); }
function selectedIds(planned) { return planned.collection.filter(item => item.selected).map(item => item.id).sort(); }


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
      scenarios: { runner: "postgres", mode: layer === "lifecycle" ? "lifecycle" : "durability", commit: planned.commit, plan_hash: planned.hash,
        planned_stages: planned.jobs[layer].planned_stages ?? postgresTestStages({ mode: "durability" }),
        stages: Object.fromEntries((planned.jobs[layer].planned_stages ?? postgresTestStages({ mode: "durability" })).map(name => [name, { exit_code: 0 }])) } };
  }
  return { needs, reports };
}

test("lifecycle-sensitive changes require deployment lifecycle verification", () => {
  for (const path of ["scripts/tempo-runtime.mjs", "scripts/tempo-deployment.mjs", "scripts/tempo-cli.mjs",
    "scripts/check-tempo-cli.mjs", "docker-compose.yml", "docker-compose.postgres-maintenance.yml",
    "scripts/verify_postgres_backup.py", "scripts/check-redis-readiness.mjs", "backend/app/postgres_store.py",
    "tests/runner/tempo-cli-fixture.mjs", "tests/unit/tempo-cli-regressions.test.ts", "backend/Dockerfile", "package-lock.json",
    ...inventory.lifecycle.sensitivePaths]) {
    const planned = plan({ paths: [path, "tests/REGRESSIONS.md"] });
    assert.equal(planned.jobs.lifecycle?.applicable, true, path);
    assert.equal(planned.jobs.lifecycle.required, true, path);
  }
});

test("unclassified root backend application modules require deployment lifecycle", () => {
  for (const path of ["backend/app/new_runtime.py", "backend/app/worker_bootstrap.py", "backend/app/storage_adapter.py",
    "backend/app/new_domain_commands.py", "backend/app/command_gateway.py", "backend/app/command_dispatch.py",
    "backend/app/background_worker.py", "backend/app/tasks.py"]) {
    for (const paths of [[path], [path, "tests/REGRESSIONS.md", "backend/tests/test_new_runtime.py"]]) {
      const planned = plan({ paths });
      assert.equal(planned.jobs.lifecycle.applicable, true, paths.join(", "));
      assert.equal(planned.jobs.lifecycle.required, true, paths.join(", "));
      assert.match(planned.jobs.lifecycle.reason, /unclassified infrastructure/);
    }
  }
});

test("unclassified backend service modules require deployment lifecycle with ordinary companions", () => {
  const ordinaryCompanions = ["tests/REGRESSIONS.md", "backend/tests/test_study_grading.py",
    "tests/unit/study-regressions.test.tsx"];
  for (const servicePath of ["backend/app/services/postgres_connection.py", "backend/app/services/storage_adapter.py",
    "backend/app/services/worker_runtime.py", "backend/app/services/deployment_state.py",
    "backend/app/services/new_domain_service.py", "backend/app/services/unclassified_future_service.py"]) {
    assert(!inventory.lifecycle.ordinaryPaths.includes(servicePath), servicePath);
    for (const paths of [[servicePath], ...ordinaryCompanions.map(companionPath => [servicePath, companionPath]),
      [servicePath, ...ordinaryCompanions]]) {
      const planned = plan({ paths });
      assert.equal(planned.jobs.lifecycle.applicable, true, paths.join(", "));
      assert.equal(planned.jobs.lifecycle.required, true, paths.join(", "));
      assert.match(planned.jobs.lifecycle.reason, /unclassified infrastructure/);
    }
  }
});

test("reviewed ordinary and sensitive service classifications survive ordinary companions", () => {
  const ordinaryCompanions = ["tests/REGRESSIONS.md", "backend/tests/test_study_grading.py",
    "tests/unit/study-regressions.test.tsx"];
  for (const [servicePath, lifecycleRequired] of [
    ["backend/app/services/study_grading.py", false],
    ["backend/app/services/opening_segmentation.py", false],
    ["backend/app/services/repertoire_statistics.py", false],
    ["backend/app/services/database_executor.py", true],
    ["backend/app/services/background_runtime.py", true],
  ]) {
    assert(existsSync(servicePath), servicePath);
    for (const paths of [[servicePath], ...ordinaryCompanions.map(companionPath => [servicePath, companionPath]),
      [servicePath, ...ordinaryCompanions]]) {
      const planned = plan({ paths });
      assert.equal(planned.jobs.lifecycle.applicable, lifecycleRequired, paths.join(", "));
      assert.equal(planned.jobs.lifecycle.required, lifecycleRequired, paths.join(", "));
    }
  }
});

test("reviewed backend domain changes with ordinary regressions omit deployment lifecycle", () => {
  for (const path of ["backend/app/services/study_grading.py", "backend/app/study_commands.py",
    "backend/app/opening_segmentation_api.py", "backend/app/study_contracts.py", "backend/app/models.py"]) {
    for (const paths of [[path], [path, "tests/REGRESSIONS.md", "backend/tests/test_study_grading.py",
      "tests/unit/study-regressions.test.tsx", "tests/browser/studies.spec.ts"]]) {
      const planned = plan({ paths });
      assert.equal(planned.jobs.lifecycle.applicable, false, paths.join(", "));
      assert.equal(planned.jobs.lifecycle.required, false, paths.join(", "));
    }
  }
});

test("ordinary product changes omit deployment lifecycle independently of browser breadth", () => {
  const ordinaryChangeGroups = [
    ...["app/components/chessboard.tsx", "backend/app/services/study_grading.py",
      "app/domain/opening-segmentation.ts", "tests/browser/studies.spec.ts", "docs/testing.md",
      ...inventory.lifecycle.ordinaryPaths].map(path => [path]),
    ["app/domain/opening-segmentation.ts", "tests/unit/opening-segmentation-regressions.test.tsx",
      "backend/tests/test_opening_segmentation.py", "tests/REGRESSIONS.md", "docs/testing.md"],
  ];
  for (const paths of ordinaryChangeGroups) {
    const planned = plan({ paths });
    assert.equal(planned.jobs.lifecycle?.applicable, false, paths.join(", "));
    assert.equal(planned.jobs.postgres.required, true);
    assert.equal(planned.scope, "targeted");
  }
  const broadBrowser = plan({ paths: ["app/components/chessboard.tsx", "tests/REGRESSIONS.md"] });
  assert.equal(broadBrowser.jobs.lifecycle.applicable, false);
  assert.equal(broadBrowser.collection.filter(item => item.selected).length, cases.length);
  assert.equal(broadBrowser.jobs.visual.applicable, true);
});

test("complete verification always requires deployment lifecycle", () => {
  for (const paths of [[], ["docs/testing.md"], ["app/components/chessboard.tsx"],
    ["backend/app/services/study_grading.py"], ["backend/app/services/new_domain_service.py"]]) {
    const planned = plan({ paths, complete: true });
    assert.equal(planned.scope, "complete");
    assert.equal(planned.jobs.lifecycle?.applicable, true);
    assert.equal(planned.jobs.lifecycle.required, true);
  }
});

test("migration and schema changes cannot omit deployment lifecycle", () => {
  for (const path of ["backend/migrations/036_new.sql", "backend/app/schema_version.py",
    "backend/app/postgres_readiness.py", "scripts/apply_postgres_migrations.py", "scripts/generate_postgres_schema.py",
    "backend/tests/test_postgres_upgrade_regressions.py", "backend/app/new_schema_policy.py"]) {
    assert.equal(plan({ paths: [path] }).jobs.lifecycle?.required, true, path);
  }
  const renamed = changedPathsFromNameStatus("R100\0backend/app/schema_version.py\0backend/app/version.py\0D\0backend/migrations/036_new.sql\0");
  assert.equal(plan({ paths: renamed }).jobs.lifecycle?.required, true);
});

test("unknown infrastructure and unavailable comparison history require lifecycle", () => {
  for (const options of [{ paths: ["infrastructure/new-contract.json"] }, { comparisonAvailable: false },
    { paths: ["scripts/new-runner.mjs"] }, { paths: [".github/workflows/new.yml"] }]) {
    assert.equal(plan(options).jobs.lifecycle?.required, true);
  }
});

test("selected lifecycle failure or missing results blocks aggregate quality", () => {
  const planned = plan({ complete: true });
  assert.equal(planned.jobs.lifecycle?.required, true);
  const valid = successfulResults(planned);
  assert.equal(evaluateQuality(planned, valid.needs, valid.reports).success, true);
  for (const result of ["failure", "cancelled", "skipped", undefined]) {
    const { needs, reports } = successfulResults(planned);
    needs.lifecycle.result = result;
    assert.equal(evaluateQuality(planned, needs, reports).success, false, String(result));
  }
  for (const missing of ["report", "command", "scenario"]) {
    const { needs, reports } = successfulResults(planned);
    if (missing === "report") delete reports.lifecycle;
    else if (missing === "command") reports.lifecycle.commands.pop();
    else delete reports.lifecycle.scenarios.stages.deployment_lifecycle;
    assert.equal(evaluateQuality(planned, needs, reports).success, false, missing);
  }
  const nonblockingPlan = structuredClone(planned);
  const nonblockingResults = successfulResults(nonblockingPlan);
  nonblockingPlan.jobs.lifecycle.required = false;
  assert.equal(evaluateQuality(nonblockingPlan, nonblockingResults.needs, nonblockingResults.reports).success, false);
  const incompletePlan = plan();
  const incompleteResults = successfulResults(incompletePlan);
  incompletePlan.scope = "complete";
  assert.equal(evaluateQuality(incompletePlan, incompleteResults.needs, incompleteResults.reports).success, false);
});

test("unselected lifecycle is explicitly inapplicable", () => {
  const planned = plan();
  assert.equal(planned.jobs.lifecycle?.applicable, false);
  assert.equal(planned.jobs.lifecycle.required, false);
  assert.match(planned.jobs.lifecycle.reason, /No lifecycle-sensitive/);
  const { needs, reports } = successfulResults(planned);
  assert.equal(needs.lifecycle.result, "skipped");
  assert.equal(evaluateQuality(planned, needs, reports).success, true);
  delete needs.lifecycle;
  assert.equal(evaluateQuality(planned, needs, reports).success, false);
  assert.throws(() => layerCommands("lifecycle", planned), /not applicable/);
});

test("lifecycle reports must match the immutable plan revision mode and scenarios", () => {
  const planned = plan({ complete: true });
  assert.equal(planned.jobs.lifecycle?.required, true);
  const modifications = [
    report => { report.planHash = "older-plan"; },
    report => { report.commit = "older-revision"; },
    report => { report.scenarios.plan_hash = "older-plan"; },
    report => { report.scenarios.commit = "older-revision"; },
    report => { report.scenarios.mode = "durability"; },
    report => { report.scenarios.planned_stages = ["cleanup"]; },
    report => { delete report.scenarios.stages.cleanup; },
    report => { report.scenarios.stages.deployment_lifecycle.exit_code = 1; },
  ];
  for (const modify of modifications) {
    const { needs, reports } = successfulResults(planned);
    modify(reports.lifecycle);
    assert.equal(evaluateQuality(planned, needs, reports).success, false);
  }
  const { needs, reports } = successfulResults(planned);
  reports.postgres.scenarios.planned_stages = ["cleanup"];
  reports.postgres.scenarios.stages = { cleanup: { exit_code: 0 } };
  assert.equal(evaluateQuality(planned, needs, reports).success, false);
});

test("full and split PostgreSQL verification preserve every existing proof", () => {
  const full = postgresTestStages({ mode: "full" });
  const durability = postgresTestStages({ mode: "durability" });
  const lifecycle = postgresTestStages({ mode: "lifecycle" });
  assert(full.includes("deployment_lifecycle"));
  assert(lifecycle.includes("deployment_lifecycle"));
  assert(!durability.includes("deployment_lifecycle"));
  for (const stage of ["schema_migrations", "priority_recovery", "background_diagnostics"]) assert(durability.includes(stage));
  const split = new Set([...durability, ...lifecycle, ...postgresTestStages({ mode: "browser" })]);
  assert.deepEqual([...split].sort(), full.filter(stage => stage !== "study_isolation").sort());
});

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
    const planned = plan(options); assert.equal(planned.collection.filter(item => item.selected).length, cases.length);
    assert.equal(planned.scope, planned.jobs.lifecycle.applicable ? "complete" : "targeted");
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
  const split = new Set([...postgresTestStages({ mode: "durability" }), ...postgresTestStages({ mode: "lifecycle" }), ...postgresTestStages({ mode: "browser" })]);
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
  assert(workflow.includes("lifecycle: ${{ steps.inventory.outputs.lifecycle }}"));
  assert(workflow.includes("  lifecycle:\n    needs: plan\n    if: needs.plan.outputs.lifecycle == 'true'\n    uses: ./.github/workflows/verify-layer.yml"));
  assert(workflow.includes("needs: [plan, frontend, backend, build, postgres, lifecycle, browser, visual, quarantine]"));
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
  const realCases = currentBrowserCases();
  for (const paths of [["docs/testing.md"], ["app/domain/opening-segmentation.ts", "tests/REGRESSIONS.md"], ["app/domain/study-exercises.ts"]]) {
    const planned = plan({ paths, cases: realCases });
    assert.deepEqual(collectBrowserCases(planned.browserGrep).map(item => item.id).sort(), selectedIds(planned));
  }
  const planned = plan({ cases: realCases });
  const offlineReplay = planned.collection.filter(item => item.file === "phone-offline-training.spec.ts" &&
    item.title === "prepared phone queue and study worker survive full offline reload and sync one review per attempt");
  assert(offlineReplay.length > 0, "Phone offline replay must resolve to actual collected cases");
  assert(offlineReplay.every(item => item.critical && item.selected));
  assert.throws(() => plan({ cases: realCases.filter(item => !offlineReplay.some(offline => offline.id === item.id)) }),
    /Missing critical browser coverage: offline replay/);
  assert.equal(selectedIds(plan({ cases: realCases })).length, inventory.critical.length);
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

test("current-main integration retains CI and segmentation regression registrations", () => {
  const regressionRegistry = readFileSync("tests/REGRESSIONS.md", "utf8");
  const harnessSource = readFileSync(new URL(import.meta.url), "utf8");
  const harnessTestNames = [...harnessSource.matchAll(/^test\("([^"]+)",/gm)].map(match => match[1]);
  assert(harnessTestNames.length > 0);
  for (const testName of harnessTestNames) {
    assert(regressionRegistry.includes(testName), `Missing CI regression registration: ${testName}`);
  }
  for (const testName of [
    "split complete verification matches the existing full command inventory",
    "test_shared_trunk_preserves_coverage_with_20_tests_instead_of_48",
    "test_pagination_requires_snapshot_and_rejects_republication",
    "test_preview_response_remains_consistent_during_concurrent_postgres_publication",
    "test_preference_dispatch_preserves_legacy_receipt_payload_and_new_snapshot",
    "test_segmentation_migration_has_unique_number_and_matches_schema_readiness",
  ]) {
    assert(regressionRegistry.includes(testName), `Missing integration regression registration: ${testName}`);
  }
  for (let acceptanceNumber = 1; acceptanceNumber <= 22; acceptanceNumber++) {
    const acceptanceId = `AS-${String(acceptanceNumber).padStart(2, "0")}`;
    assert(regressionRegistry.includes(`| ${acceptanceId} |`), `Missing segmentation acceptance mapping: ${acceptanceId}`);
  }
});


test("every current browser spec belongs to exactly one complete family", () => {
  const currentSpecs = readdirSync("tests/browser").filter(file => file.endsWith(".spec.ts")).sort();
  assert.deepEqual(Object.values(inventory.families).flat().sort(), currentSpecs);
  validateInventory(currentSpecs);
  assert.throws(() => validateInventory([...currentSpecs, "unclassified.spec.ts"]), /Unclassified/);
  const duplicateSpecInventory = { ...inventory, families: { ...inventory.families, duplicate: [currentSpecs[0]] } };
  assert.throws(() => validateInventory(currentSpecs, duplicateSpecInventory), /multiple inventory families/);
});

test("reviewed source mappings reject missing families and duplicate or ambiguous paths", () => {
  for (const sourceMapping of inventory.sources) {
    assert(sourceMapping.reason?.trim(), "Every narrowed source group needs its consumer rationale");
    for (const mappedPath of sourceMapping.paths) assert(existsSync(mappedPath), `Stale source mapping: ${mappedPath}`);
  }
  for (const invalidMapping of [
    { paths: ["unclassified.ts"], families: ["missing"] },
    { paths: ["unclassified.ts"], families: [] },
    { paths: ["unclassified.ts"], families: ["training", "training"] },
    { paths: ["unclassified.ts"], families: ["pinned"] },
    { paths: ["/absolute.ts"], families: ["training"] },
    { paths: ["../outside.ts"], families: ["training"] },
    { paths: [""], families: ["training"] },
    { paths: [inventory.sources[0].paths[0]], families: ["studies"] },
  ]) assert.throws(() => plan({ sourceInventory: { ...inventory, sources: [...inventory.sources, invalidMapping] } }), /source mapping/i);
});

test("demoted critical cases remain required by their complete browser families", () => {
  const realCases = currentBrowserCases();
  for (const demoted of demotedCriticalCases) {
    assert(inventory.families[demoted.family].includes(demoted.file));
    const planned = plan({ cases: realCases, paths: [`tests/browser/${demoted.file}`] });
    const matchingCases = planned.collection.filter(item => item.file === demoted.file && item.title === demoted.title);
    assert(matchingCases.length, `Demoted case disappeared: ${demoted.title}`);
    assert(matchingCases.every(item => !item.critical && item.selected && item.nightly && item.release), demoted.title);
    assert.deepEqual(planned.families, [demoted.family]);
    assert(planned.collection.filter(item => inventory.families[demoted.family].includes(item.file)).every(item => item.selected));
  }
});

test("ordinary prose and standalone core tests select only six global browser smoke cases", () => {
  assert.equal(inventory.critical.length, 6);
  const proseAndCorePaths = ["docs/testing.md", "README.md", "CONTRIBUTING.md", "AGENTS.md", "THIRD_PARTY_NOTICES.md",
    "app/components/README.md", "tests/browser/README.md", "tests/REGRESSIONS.md", "tests/unit/study-regressions.test.tsx", "backend/tests/test_studies.py"];
  const realCases = currentBrowserCases();
  const criticalIds = plan({ cases: realCases }).collection.filter(item => item.critical).map(item => item.id).sort();
  assert.equal(criticalIds.length, 6);
  for (const paths of [...proseAndCorePaths.map(path => [path]), proseAndCorePaths]) {
    const planned = plan({ cases: realCases, paths });
    assert.equal(planned.scope, "targeted", paths.join(", "));
    assert.deepEqual(planned.families, []);
    assert.deepEqual(selectedIds(planned), criticalIds);
    assert(mandatoryLayers.every(layer => planned.jobs[layer].required));
  }
});

test("mapped leaf changes retain critical plus their family with regression additions", () => {
  const realCases = currentBrowserCases();
  const planned = plan({ cases: realCases, paths: ["app/domain/opening-segmentation.ts",
    "tests/unit/opening-segmentation-regressions.test.tsx", "backend/tests/test_opening_segmentation.py", "tests/REGRESSIONS.md"] });
  assert.equal(planned.scope, "targeted");
  assert.deepEqual(planned.families, ["repertoire"]);
  assert.equal(planned.jobs.visual.applicable, false);
  const expectedIds = planned.collection.filter(item => item.critical || inventory.families.repertoire.includes(item.file)).map(item => item.id).sort();
  assert.deepEqual(selectedIds(planned), expectedIds);
});

test("shared subsystem sources select complete consumer families without unrelated families", () => {
  const examples = [
    ["app/lib/opening-evidence-recovery-policy.ts", ["training"]],
    ["app/hooks/use-opening-evidence-recovery.ts", ["training"]],
    ["backend/app/services/postgres_opening_evidence.py", ["training"]],
    ["app/lib/opening-evidence-review.ts", ["defense", "training"]],
    ["app/domain/study-exercises.ts", ["studies", "training"]],
    ["backend/app/services/study_grading.py", ["studies", "training"]],
    ["backend/app/services/opening_segmentation.py", ["repertoire", "training"]],
    ["app/lib/discovery-preview-scheduler.ts", ["discoveries", "training"]],
    ["app/lib/comparison.ts", ["games"]],
  ];
  for (const [sourcePath, expectedFamilies] of examples) {
    const planned = plan({ paths: [sourcePath, "tests/REGRESSIONS.md"] });
    assert.equal(planned.scope, "targeted", sourcePath);
    assert.deepEqual(planned.families, expectedFamilies, sourcePath);
    const requiredSpecs = new Set(expectedFamilies.flatMap(family => inventory.families[family]));
    assert.deepEqual(selectedIds(planned), planned.collection.filter(item => item.critical || requiredSpecs.has(item.file)).map(item => item.id).sort(), sourcePath);
  }
  for (const [sourcePath, expectedFamilies] of [["app/views/studies_view.tsx", ["studies", "training"]], ["app/views/comparison_view.tsx", ["games"]]]) {
    const planned = plan({ paths: [sourcePath] });
    assert.deepEqual(planned.families, expectedFamilies);
    assert(planned.jobs.visual.required, "Narrow browser families must retain pinned rendering checks");
  }
});

test("cross-cutting browser infrastructure and uncertain inputs require the complete matrix", () => {
  const crossCuttingPaths = ["playwright.config.ts", "tests/browser/ui-fixtures.ts", "tests/browser/product-fixtures.ts",
    "tests/browser/observability.ts", "tests/fixtures/study-grading.json", "tests/unit/keyboard-board-fixture.tsx", "backend/tests/conftest.py",
    "app/components/board/chessboard.tsx", "app/state/training-store.ts", "app/domain/schemas/index.ts", "app/lib/operation-status.ts",
    "app/lib/offline-training.ts", "backend/app/main.py", "backend/app/database.py", "backend/migrations/001_initial.sql",
    "scripts/ci-verification-plan.mjs", "scripts/test-postgres-docker.mjs", "tests/runner/ci-reliability.test.mjs",
    "package.json", "package-lock.json", ".github/workflows/pages.yml", "unclassified/new-source.ts", "unclassified/new-file.md"];
  for (const path of crossCuttingPaths) {
    const planned = plan({ paths: ["app/domain/opening-segmentation.ts", path] });
    assert.equal(planned.scope, planned.jobs.lifecycle.applicable ? "complete" : "targeted", path);
    assert.equal(selectedIds(planned).length, cases.length, path);
    assert(planned.jobs.visual.required, path);
  }
  assert.equal(selectedIds(plan({ comparisonAvailable: false })).length, cases.length);
});

test("renamed and copied subsystem paths union both complete family selections", () => {
  for (const status of ["R100", "C100"]) {
    const paths = changedPathsFromNameStatus(`${status}\0app/lib/comparison.ts\0app/domain/study-exercises.ts\0`);
    assert.deepEqual(plan({ paths }).families, ["games", "studies", "training"]);
  }
  assert.deepEqual(plan({ paths: changedPathsFromNameStatus("D\0app/lib/comparison.ts\0") }).families, ["games"]);
});

test("complete verification and non-PR boundaries retain the full collected browser matrix", () => {
  const realCases = currentBrowserCases();
  const full = plan({ cases: realCases, complete: true, paths: ["docs/testing.md"] });
  assert.deepEqual(selectedIds(full), realCases.map(item => item.id).sort());
  for (const project of ["chromium", "firefox", "webkit"]) assert(full.collection.some(item => item.selected && item.project === project));
  assert(!layerCommands("browser", full).at(-1)[2].includes("--browser-grep"));
  const workflow = readFileSync(".github/workflows/pages.yml", "utf8");
  const selectionBlock = workflow.match(/          if \[ "\$TEMPO_EVENT" = pull_request \]; then\n[\s\S]*?          fi/)?.[0];
  assert(selectionBlock, "Missing explicit PR versus complete planning boundary");
  for (const event of ["pull_request", "push", "schedule", "merge_group", "workflow_dispatch"]) {
    const result = spawnSync("bash", ["-c", 'node() { printf "%s\\n" "$@"; }\n' + selectionBlock], {
      encoding: "utf8", env: { ...process.env, TEMPO_EVENT: event, TEMPO_BASE: "base-revision" },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(result.stdout.trim().split("\n"), ["scripts/ci-verification-plan.mjs", ...(event === "pull_request" ? ["--base", "base-revision"] : ["--complete"])]);
  }
});

test("browser quality rejects missing extra duplicate wrong-project and stale results", () => {
  const invalidResults = [
    report => report.tests.pop(),
    report => report.tests.push({ id: "extra:chromium", status: "passed", retries: 0 }),
    report => { report.tests[0].id = `${report.tests[0].id.startsWith("f") ? "e" : "f"}${report.tests[0].id.slice(1)}`; },
    report => { report.tests[0] = { ...report.tests[1] }; },
    report => { report.tests[0].id = report.tests[0].id.replace(/:[^:]+$/, report.tests[0].id.endsWith(":chromium") ? ":webkit" : ":chromium"); },
    report => { report.commit = "older-revision"; },
    report => { report.planHash = "another-plan"; },
    report => { report.tests[0].status = "skipped"; },
    report => { report.tests[0].status = "failed"; },
    report => { report.tests[0].retries = 1; },
  ];
  for (const complete of [false, true]) {
    const planned = plan({ complete, cases: currentBrowserCases() });
    for (const invalidate of invalidResults) {
      const { needs, reports } = successfulResults(planned);
      assert(evaluateQuality(planned, needs, reports).success);
      invalidate(reports.browser);
      assert.equal(evaluateQuality(planned, needs, reports).success, false, invalidate.toString());
    }
  }
});

test("documented global browser smoke count and titles match inventory and real collection", () => {
  const documentation = readFileSync("docs/testing.md", "utf8");
  const documentedCount = documentation.match(/^Global critical browser smoke: \*\*(\d+) cases\*\*\.$/m);
  assert(documentedCount, "Document the authoritative smoke count explicitly");
  const documentedGlobalCases = [...documentation.matchAll(/^\| `([^`]+)` \| `([^`]+)` \| global \|/gm)].map(match => `${match[1]}:${match[2]}`).sort();
  assert.deepEqual(documentedGlobalCases, inventory.critical.map(item => `${item.file}:${item.title}`).sort());
  assert.equal(Number(documentedCount[1]), inventory.critical.length);
  assert.equal(Number(documentedCount[1]), plan({ cases: currentBrowserCases() }).collection.filter(item => item.critical).length);
  for (const demoted of demotedCriticalCases) assert(documentation.includes(`| \`${demoted.file}\` | \`${demoted.title}\` | family |`), demoted.title);
});
