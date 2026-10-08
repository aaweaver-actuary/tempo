import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { Chess } from "chess.js";
import { buildPostgresPlaywrightArguments, parsePostgresTestOptions } from "../../scripts/postgres-test-options.mjs";
import { backgroundWorkloadConsumers, executeIsolatedBackgroundWorkload, executePostgresTestPlan, postgresTestStages } from "../../scripts/postgres-test-plan.mjs";
import { assertNoCompletedFixtureConflict, backgroundPublicationPgn,
  repertoireLimitRecreationPgn, studyDurabilityPgn } from "../../scripts/postgres-test-fixture.mjs";
import { createScenarioTimer } from "../../scripts/test-scenario-timings.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const parse = (args) => parsePostgresTestOptions(args, {}, root);
const fullStages = [
  "compose_config", "image_build", "maintenance_cli", "startup", "service_health",
  "background_budget", "operation_recovery", "schema_migrations", "priority_recovery", "background_diagnostics", "deployment_lifecycle", "background_workloads",
  "threat_candidate_upsert", "command_recreation", "backup_restore", "browser",
  "study_isolation", "study_durability", "cleanup",
];
const browserStages = ["compose_config", "image_build", "startup", "service_health", "browser", "cleanup"];
const directMeasurement = async (_name, action) => action();

function temporaryDirectory(testContext) {
  const directory = mkdtempSync(join(tmpdir(), "tempo-runner-speed-"));
  testContext.after(() => rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test("default PostgreSQL gate retains every recovery check and one unfiltered browser matrix", () => {
  const options = parse([]);
  assert.equal(options.mode, "full");
  assert.deepEqual(postgresTestStages(options), fullStages);
  assert.deepEqual(buildPostgresPlaywrightArguments(options), ["playwright", "test"]);
});

test("focused PostgreSQL browser execution excludes maintenance and durability scenarios", () => {
  assert.deepEqual(postgresTestStages(parse(["--mode", "browser"])), browserStages);
  const options = parse(["--browser-grep", "Builder [review]"]);
  assert.equal(options.mode, "browser");
  assert.deepEqual(postgresTestStages(options), browserStages);
  assert.deepEqual(buildPostgresPlaywrightArguments(options), ["playwright", "test", "--grep", "Builder [review]"]);
});

test("durability mode and legacy skip-browser retain ordinary recovery without deployment lifecycle", () => {
  const expected = fullStages.filter((stage) => !["browser", "study_isolation", "deployment_lifecycle"].includes(stage));
  assert.deepEqual(postgresTestStages(parse(["--mode", "durability"])), expected);
  assert.deepEqual(postgresTestStages(parse(["--skip-browser"])), expected);
  assert.equal(parse(["--mode", "durability"]).skipBrowser, true);
});

test("priority benchmark mode runs only its isolated PostgreSQL measurement", () => {
  const options = parse(["--mode", "priority-benchmark"]);
  assert.deepEqual(postgresTestStages(options), [
    "compose_config", "image_build", "startup", "service_health", "priority_benchmark", "cleanup",
  ]);
  assert.throws(() => parse(["--mode", "priority-benchmark", "--browser-grep", "Train"]),
    /cannot be combined/);
});

test("standalone lifecycle mode requires the complete rehearsal without parent startup or browser work", () => {
  assert.deepEqual(postgresTestStages(parse(["--mode", "lifecycle"])), [
    "compose_config", "image_build", "maintenance_cli", "deployment_lifecycle", "cleanup",
  ]);
  assert.throws(() => parse(["--mode", "lifecycle", "--browser-file", "studies.spec.ts"]), /cannot be combined/);
});

test("standalone lifecycle prepares only missing dependency images on cold and warm Docker daemons", async () => {
  const runnerSource = readFileSync(join(root, "scripts/test-postgres-docker.mjs"), "utf8");
  const imageBuildStartIndex = runnerSource.indexOf("  image_build: async () => {");
  const maintenanceCliStartIndex = runnerSource.indexOf("  maintenance_cli: async () => {", imageBuildStartIndex);
  assert(imageBuildStartIndex >= 0 && maintenanceCliStartIndex > imageBuildStartIndex);
  for (const mode of ["lifecycle", "durability", "full", "browser", "priority-benchmark"]) {
    for (const cachedServices of [[], ["postgres"], ["postgres", "redis"]]) {
      const servicesWithAvailableImages = new Set(cachedServices);
      const imagePreparationCommands = [];
      const registryAcquisitions = [];
      const imagePreparationContext = { options: { mode }, compose: ["compose"], resourcesCreated: false,
        run: (command, args) => {
          assert.equal(command, "docker");
          imagePreparationCommands.push(args);
          if (args.includes("build")) servicesWithAvailableImages.add("application");
          if (args.includes("pull")) {
            const pullArguments = args.slice(args.indexOf("pull") + 1);
            assert.deepEqual(Array.from(pullArguments), ["--policy", "missing", "postgres", "redis"],
              "Dependency preparation must use Compose's missing-only policy for exactly PostgreSQL and Redis");
            for (const service of pullArguments.slice(2)) {
              if (servicesWithAvailableImages.has(service)) continue;
              registryAcquisitions.push(service);
              servicesWithAvailableImages.add(service);
            }
          }
        } };
      await runInNewContext(`({${runnerSource.slice(imageBuildStartIndex, maintenanceCliStartIndex)}})`, imagePreparationContext).image_build();
      assert(imagePreparationContext.resourcesCreated, "Owned image cleanup is armed before preparation");
      assert(servicesWithAvailableImages.has("application"));
      if (mode === "lifecycle") {
        assert.equal(imagePreparationCommands.filter(args => args.includes("pull")).length, 1);
        assert.deepEqual(registryAcquisitions, ["postgres", "redis"].filter(service => !cachedServices.includes(service)),
          "Only uncached dependencies may require registry access");
        for (const service of ["postgres", "redis"])
          assert(servicesWithAvailableImages.has(service), `Rehearsal requires the ${service} image before child startup`);
      } else {
        assert(!imagePreparationCommands.some(args => args.includes("pull")), "Other modes retain their existing startup prerequisites");
      }
      assert(!imagePreparationCommands.some(args => args.includes("up")), "Image preparation must not start parent services");
    }
  }
});

test("standalone lifecycle propagates missing dependency acquisition failures with cleanup armed", async () => {
  const runnerSource = readFileSync(join(root, "scripts/test-postgres-docker.mjs"), "utf8");
  const imageBuildStartIndex = runnerSource.indexOf("  image_build: async () => {");
  const maintenanceCliStartIndex = runnerSource.indexOf("  maintenance_cli: async () => {", imageBuildStartIndex);
  assert(imageBuildStartIndex >= 0 && maintenanceCliStartIndex > imageBuildStartIndex);
  const acquisitionFailure = new Error("Missing Redis image could not be acquired");
  const imagePreparationContext = { options: { mode: "lifecycle" }, compose: ["compose"], resourcesCreated: false,
    run: (_command, args) => {
      if (args.includes("pull")) throw acquisitionFailure;
    } };
  const actions = runInNewContext(`({${runnerSource.slice(imageBuildStartIndex, maintenanceCliStartIndex)}})`, imagePreparationContext);
  await assert.rejects(actions.image_build(), error => error === acquisitionFailure);
  assert(imagePreparationContext.resourcesCreated, "Acquisition failure must leave owned image cleanup armed");
});

test("lifecycle rehearsal restores full-mode applications after failure and never starts the standalone parent", async () => {
  const source = readFileSync(join(root, "scripts/test-postgres-docker.mjs"), "utf8");
  const start = source.indexOf("  deployment_lifecycle: async () => {");
  const end = source.indexOf("  background_workloads: async () => {", start);
  assert(start >= 0 && end > start);
  for (const mode of ["full", "lifecycle"]) for (const shouldFail of [false, true]) {
    const calls = [];
    const rehearsalFailure = new Error("rehearsal failed");
    const context = { options: { mode }, project: "fixture", environment: {}, candidateRevision: "revision",
      buildLabels: "labels", compose: ["compose"], join, process: { cwd: () => root },
      verifyTempoCliLifecycle: async () => { calls.push("rehearsal"); if (shouldFail) throw rehearsalFailure; },
      run: (_command, args) => calls.push(args.includes("stop") ? "stop" : "restore"),
      waitForReady: async () => calls.push("ready") };
    const actions = runInNewContext(`({${source.slice(start, end)}})`, context);
    if (shouldFail) await assert.rejects(actions.deployment_lifecycle(), error => error === rehearsalFailure);
    else await actions.deployment_lifecycle();
    assert.deepEqual(calls, mode === "full" ? ["stop", "rehearsal", "restore", "ready"] : ["rehearsal"]);
  }
});

test("valid opponent-branch durability and background fixtures prescribe one White response per position", () => {
  for (const [pgn, expectedGameCount, initialResponse] of [
    [studyDurabilityPgn, 3, "e2e4"], [backgroundPublicationPgn, 1, "c2c4"],
  ]) {
    const prescribedResponses = new Map();
    const games = pgn.trim().split(/(?=\[Event )/).filter(Boolean);
    assert.equal(games.length, expectedGameCount);
    for (const game of games) {
      const parsed = new Chess();
      parsed.loadPgn(game);
      const board = new Chess();
      for (const move of parsed.history({ verbose: true })) {
        if (board.turn() === "w") {
          const position = board.fen().split(" ").slice(0, 4).join(" ");
          const response = `${move.from}${move.to}${move.promotion ?? ""}`;
          const priorResponse = prescribedResponses.get(position);
          assert(priorResponse === undefined || priorResponse === response,
            `${position} prescribes ${priorResponse} and ${response}`);
          prescribedResponses.set(position, response);
        }
        board.move(move);
      }
      assert.equal(board.turn(), "b", "Every line ends after a trained-player response");
    }
    assert.equal(prescribedResponses.get(new Chess().fen().split(" ").slice(0, 4).join(" ")), initialResponse);
  }
});

test("repertoire limit recreation fixture includes its final White response", () => {
  const board = new Chess();
  board.loadPgn(repertoireLimitRecreationPgn);
  assert.equal(board.turn(), "b", "Recreation must reach study admission without a missing White response");
  const runnerSource = readFileSync(join(root, "scripts/test-postgres-docker.mjs"), "utf8");
  assert.match(runnerSource, /importFixture\("repertoire-limit-recreation\.pgn", repertoireLimitRecreationPgn\)/);
});

test("repertoire limit recreation fixture survives backup then leaves unrelated study state intact", async () => {
  const runnerSource = readFileSync(join(root, "scripts/test-postgres-docker.mjs"), "utf8");
  const scenarioStart = runnerSource.indexOf('  command_recreation: async () => {');
  const scenarioEnd = runnerSource.indexOf('  browser: async () => {', scenarioStart);
  assert(scenarioStart > 0 && scenarioEnd > scenarioStart);
  const fixtureRows = new Map([['unrelated', { id: 'unrelated', new_cards_per_day: null }]]);
  let settings = { new_cards_per_day: 10 };
  let backupVerified = false;
  let overrideReplayed = false;
  const context = {
    assert, compose: ['compose'], randomBytes: () => Buffer.from('fixture'),
    workloadConsumers: ['background-worker','background-scheduler','defense-engine'],
    runPrefixApplicationProof: () => {},
    repertoireLimitRecreationPgn, limitRecreationRepertoireId: null,
    console: { log() {} },
    importFixture: async () => {
      fixtureRows.set('recreation', { id: 'recreation', new_cards_per_day: null });
      return { repertoire_id: 'recreation' };
    },
    waitForStudyableImport: async () => {}, waitForReady: async () => {},
    get: async (path) => {
      if (path === 'settings') return { ...settings };
      if (path === 'repertoires') return { repertoires: [...fixtureRows.values()] };
      if (path === 'queue/today') return { cards: [...fixtureRows.keys()].map(id => ({ queue_entry_id: id })) };
      if (path.startsWith('operations/')) return { state: 'complete' };
      throw new Error(`Unexpected read: ${path}`);
    },
    apiRequest: async (path, options) => {
      assert.equal(path, 'settings');
      settings = JSON.parse(options.body);
      return { ok: true, body: { cancel: async () => {} } };
    },
    confirm: async () => ({}),
    postCommand: async (path, payload, options) => {
      if (options.method === 'DELETE') {
        assert(backupVerified, 'Persisted override must be present when backup is verified');
        assert.equal(path, 'repertoires/recreation');
        fixtureRows.delete('recreation');
        return {};
      }
      assert.equal(path, 'repertoires/recreation/settings');
      const repertoire = fixtureRows.get('recreation');
      overrideReplayed = repertoire.new_cards_per_day !== null;
      repertoire.new_cards_per_day = payload.new_cards_per_day;
      repertoire.effective_new_cards_per_day = payload.new_cards_per_day;
      return {};
    },
    run: (command, argumentsList) => {
      if (argumentsList.includes('/source/scripts/verify_postgres_backup.py')) {
        assert.equal(fixtureRows.get('recreation').new_cards_per_day, 11);
        backupVerified = true;
      }
    },
  };
  // Execute the real scenario bodies with only I/O replaced. This protects the
  // fixture lifecycle, including replay and backup ordering, without Docker.
  const actions = runInNewContext(`({${runnerSource.slice(scenarioStart, scenarioEnd)}})`, context);
  await actions.command_recreation();
  assert(overrideReplayed);
  assert(fixtureRows.has('recreation'), 'Keep the fixture for backup verification');
  await actions.backup_restore();
  assert(backupVerified);
  assert.deepEqual([...fixtureRows.keys()], ['unrelated'], 'Owned recreation entries cannot precede the next study workflow');
});

test("full browser coverage creates a fresh durability database before study commands", () => {
  const browserIndex = fullStages.indexOf("browser");
  assert.equal(fullStages[browserIndex + 1], "study_isolation");
  assert.equal(fullStages[browserIndex + 2], "study_durability");
  assert.equal(browserStages.includes("study_isolation"), false);
});

test("full and durability modes cannot silently narrow their browser coverage", () => {
  for (const mode of ["full", "durability", "lifecycle"]) {
    assert.throws(() => parse(["--mode", mode, "--browser-grep", "Builder"]), /cannot be combined/);
  }
  assert.throws(() => parse(["--skip-browser", "--mode", "full"]), /cannot be combined/);
  assert.throws(() => parse(["--skip-browser", "--mode", "browser"]), /cannot be combined/);
  assert.throws(() => parsePostgresTestOptions([], { TEMPO_PG_BROWSER_GREP: "Builder" }), /clear inherited/);
});

test("invalid and duplicate mode or browser options fail before infrastructure work", () => {
  for (const args of [["--mode"], ["--mode", "unknown"], ["--mode", "browser", "--mode", "full"],
    ["--browser-grep", ""], ["--browser-grep", "["], ["--unexpected"],
    ["--browser-grep", "A", "--browser-grep", "B"]]) {
    assert.throws(() => parse(args));
  }
  assert.throws(() => postgresTestStages({ mode: "unknown" }), /Unknown/);
});

test("file focus is explicit, discrete, and restricted to an existing browser basename", (context) => {
  const directory = temporaryDirectory(context);
  mkdirSync(join(directory, "tests/browser"), { recursive: true });
  writeFileSync(join(directory, "tests/browser/fixture.spec.ts"), "");
  const options = parsePostgresTestOptions(["--browser-file", "fixture.spec.ts"], {}, directory);
  assert.equal(options.mode, "browser");
  assert.deepEqual(buildPostgresPlaywrightArguments(options), ["playwright", "test", join("tests", "browser", "fixture.spec.ts")]);
  for (const file of ["../fixture.spec.ts", "..\\fixture.spec.ts", "/fixture.spec.ts", "missing.spec.ts"]) {
    assert.throws(() => parsePostgresTestOptions(["--browser-file", file], {}, directory));
  }
});

for (const mode of ["full", "browser", "durability", "lifecycle", "priority-benchmark"]) {
  test(`${mode} --list exposes the executable plan without Docker, ports, secrets, or timing files`, (context) => {
    const directory = temporaryDirectory(context);
    const result = spawnSync(process.execPath, [resolve(root, "scripts/test-postgres-docker.mjs"), "--list", "--mode", mode], {
      cwd: directory, encoding: "utf8", timeout: 5_000,
      env: { PATH: "", TEMPO_TEST_TIMING_DIR: join(directory, "timings") },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout).stages, postgresTestStages({ mode }));
    assert.equal(JSON.parse(result.stdout).mode, mode);
    assert.deepEqual(readdirSync(directory), []);
  });

  test(`${mode} executor invokes exactly its planned actions once, including cleanup`, async () => {
    const called = [];
    const stages = postgresTestStages({ mode });
    const actions = Object.fromEntries(stages.map((name) => [name, async () => { called.push(name); }]));
    await executePostgresTestPlan(stages, actions, directMeasurement);
    assert.deepEqual(called, stages);
  });
}

test("scenario failure captures diagnostics before cleanup and never runs later tests", async () => {
  const called = [];
  const failure = new Error("browser failed");
  await assert.rejects(executePostgresTestPlan(["browser", "study_durability", "cleanup"], {
    browser: () => { called.push("browser"); throw failure; },
    study_durability: () => called.push("study_durability"),
    cleanup: () => called.push("cleanup"),
  }, directMeasurement, () => called.push("diagnostics")), (error) => error === failure);
  assert.deepEqual(called, ["browser", "diagnostics", "cleanup"]);
});

test("failed startup still cleans up and a cleanup failure cannot mask the original failure", async () => {
  const failure = new Error("startup failed");
  const cleanupFailure = new Error("cleanup failed");
  await assert.rejects(executePostgresTestPlan(["startup", "cleanup"], {
    startup: () => { throw failure; }, cleanup: () => { throw cleanupFailure; },
  }, directMeasurement), (error) => error instanceof AggregateError
    && error.errors[0] === failure && error.errors[1] === cleanupFailure);
});

test("cleanup failure after otherwise passing checks still fails the invocation", async () => {
  await assert.rejects(executePostgresTestPlan(["browser", "cleanup"], {
    browser: () => {}, cleanup: () => { throw new Error("cleanup failed"); },
  }, directMeasurement), /cleanup failed/);
});

test("falsy rejection reasons cannot turn a failing scenario into success", async () => {
  await assert.rejects(executePostgresTestPlan(["browser", "cleanup"], {
    browser: () => Promise.reject(null), cleanup: () => {},
  }, directMeasurement), /null/);
});

test("missing or duplicate planned actions fail while cleanup still runs exactly once", async () => {
  let cleanups = 0;
  const actions = { startup: () => {}, cleanup: () => { cleanups += 1; } };
  await assert.rejects(executePostgresTestPlan(["missing", "cleanup"], actions, directMeasurement), /Missing/);
  await assert.rejects(executePostgresTestPlan(["startup", "startup", "cleanup"], actions, directMeasurement), /unique/);
  assert.equal(cleanups, 2);
});

test("timings retain successful, failed, and cleanup durations without recording secret-bearing errors", async (context) => {
  const directory = temporaryDirectory(context);
  const outputPath = join(directory, "nested", "postgres-scenarios-full.json");
  let now = 1_000;
  const measure = createScenarioTimer(outputPath, { mode: "full", planned_stages: ["startup", "browser", "cleanup"] }, () => now);
  assert.equal(await measure("startup", async () => { now += 250; return 42; }), 42);
  await assert.rejects(measure("browser", async () => { now += 1_000; throw new Error("secret-canary"); }), /secret-canary/);
  await measure("cleanup", () => { now += 100; });
  const serialized = readFileSync(outputPath, "utf8");
  const report = JSON.parse(serialized);
  assert.deepEqual(report.stages, {
    startup: { duration_seconds: 0.25, exit_code: 0 },
    browser: { duration_seconds: 1, exit_code: 1 },
    cleanup: { duration_seconds: 0.1, exit_code: 0 },
  });
  assert.equal(report.mode, "full");
  assert.equal(serialized.includes("secret-canary"), false);
  assert.deepEqual(readdirSync(join(directory, "nested")), ["postgres-scenarios-full.json"]);
  await assert.rejects(measure("startup", () => {}), /Duplicate/);
});

test("completed invalid study fixtures fail immediately instead of polling for impossible admission", () => {
  assert.throws(() => assertNoCompletedFixtureConflict({
    scan_status: "idle", status: "needs_repair", issues: [{ kind: "conflicting_move" }],
  }, "fixture-id"), /fixture-id.*needs_repair.*conflicting_move/);
});

test("pending integrity generations and clean fixtures are not rejected as completed conflicts", () => {
  for (const scanStatus of ["queued", "running", "retrying"]) {
    assert.doesNotThrow(() => assertNoCompletedFixtureConflict({ scan_status: scanStatus, status: "needs_repair" }, "fixture"));
  }
  assert.doesNotThrow(() => assertNoCompletedFixtureConflict({ scan_status: "idle", status: "clean" }, "fixture"));
  assert.doesNotThrow(() => assertNoCompletedFixtureConflict({ scan_status: "idle", status: "unchecked" }, "fixture"));
});

test("full tier remains unfiltered while UI and browser tiers use browser-only PostgreSQL execution", () => {
  for (const tier of ["full", "ui", "browser"]) {
    const result = spawnSync(process.execPath, ["scripts/test-all.mjs", "--list", tier], {
      cwd: root, encoding: "utf8", timeout: 5_000,
    });
    assert.equal(result.status, 0, result.stderr);
    const { stages } = JSON.parse(result.stdout);
    const postgres = stages.filter((stage) => stage.name === "postgres_docker");
    assert.equal(postgres.length, 1);
    assert.deepEqual(postgres[0].args, tier === "full"
      ? ["scripts/test-postgres-docker.mjs"]
      : ["scripts/test-postgres-docker.mjs", "--mode", "browser"]);
  }
});

test("Make browser and durability select different scopes without changing the release entry point", () => {
  for (const [target, expected] of [
    ["browser", "node scripts/test-postgres-docker.mjs --mode browser"],
    ["docker-durability", "node scripts/test-postgres-docker.mjs --mode durability"],
    ["full", "node scripts/test-all.mjs full"],
  ]) {
    const result = spawnSync("make", ["-n", target], { cwd: root, encoding: "utf8", timeout: 5_000 });
    assert.equal(result.status, 0, result.stderr);
    assert(result.stdout.includes(expected));
  }
  const focusedWrapper = readFileSync(join(root, "scripts/run-focused-postgres-browser.mjs"), "utf8");
  assert(focusedWrapper.includes('"--mode", "browser", flag, value'));
});

test("Docker context excludes generated test credentials and local cache churn", () => {
  const patterns = readFileSync(join(root, ".dockerignore"), "utf8").split(/\r?\n/);
  for (const expected of [".tempo-pg-test-secrets-*", ".dev-copies", ".pytest_cache", "**/__pycache__", "test-results"]) {
    assert(patterns.includes(expected), `Missing Docker exclusion: ${expected}`);
  }
});

test("disposable PostgreSQL backup handles INT and TERM while waiting on its sleeper", () => {
  const composeSource = readFileSync(join(root, "docker-compose.postgres.test.yml"), "utf8");
  const backupServiceSource = composeSource.match(/^  postgres-backup:\r?\n([\s\S]*?)(?=^  \S)/m)?.[1];
  assert(backupServiceSource, "Disposable backup service must remain in the Compose lifecycle");
  assert.match(backupServiceSource, /^    entrypoint: \[sh\]$/m);
  const quotedBackupCommand = backupServiceSource.match(/^    command: \[-c, (".*")\]$/m)?.[1];
  assert(quotedBackupCommand, "Backup idle command must be one shell argument");
  const backupCommand = JSON.parse(quotedBackupCommand);
  assert.equal(backupCommand, "trap 'exit 0' INT TERM; sleep infinity & wait",
    "Install both shutdown traps before waiting on the background sleeper; PID 1 cannot rely on default signal handling");
  const syntaxCheck = spawnSync("sh", ["-n", "-c", backupCommand], { encoding: "utf8", timeout: 5_000 });
  assert.ifError(syntaxCheck.error);
  assert.equal(syntaxCheck.status, 0, syntaxCheck.stderr);
});

test("scenario dispatch builds once and all startup paths forbid implicit rebuilds", () => {
  const source = readFileSync(join(root, "scripts/test-postgres-docker.mjs"), "utf8");
  assert.equal(source.split('[...compose, "build"]').length - 1, 1);
  assert.equal(source.includes('[...compose, "up", "--build"'), false);
  assert.equal(source.includes('[...compose, "up", "-d"'), false);
  assert(source.includes('[...compose, "up", "--no-build", "-d"]'));
  assert(source.includes('[...compose, "down", "--rmi", "local", "-v"]'));
  assert(source.includes('executePostgresTestPlan(stages, actions, measureScenario'));
});

test("CI isolates cancellation, caches dependencies and requires split complete verification", () => {
  const workflow = readFileSync(join(root, ".github/workflows/pages.yml"), "utf8");
  assert(workflow.includes("github.event.pull_request.number || github.ref"));
  assert(workflow.includes("cancel-in-progress: ${{ github.event_name == 'pull_request' }}"));
  const layers = readFileSync(join(root, ".github/workflows/verify-layer.yml"), "utf8");
  assert(layers.includes("cache: pip"));
  assert.equal((workflow + layers).split("uses: Swatinem/rust-cache@v2").length - 1, 2);
  assert(workflow.includes("needs: [plan, frontend, backend, build, postgres, lifecycle, browser, visual, quarantine]"));
  assert(workflow.includes("node scripts/ci-quality.mjs"));
  assert(workflow.includes("needs.plan.outputs.scope == 'complete'"));
  assert(workflow.includes("group: tempo-pages-deployment\n      cancel-in-progress: false"));
});


test("timing write failure preserves the original scenario error and still permits cleanup", async (context) => {
  const directory = temporaryDirectory(context);
  const blockedDirectory = join(directory, "not-a-directory");
  writeFileSync(blockedDirectory, "blocked");
  const measure = createScenarioTimer(join(blockedDirectory, "report.json"), {});
  const testFailure = new Error("original scenario failure");
  let cleaned = false;
  await assert.rejects(executePostgresTestPlan(["browser", "cleanup"], {
    browser: () => { throw testFailure; },
    cleanup: () => { cleaned = true; },
  }, measure), (error) => {
    assert(error instanceof AggregateError);
    assert(error.errors[0] instanceof AggregateError);
    assert.equal(error.errors[0].errors[0], testFailure);
    return true;
  });
  assert.equal(cleaned, true);
});

test("background workload prevents scheduler claims and restores dispatch after failure", async () => {
  const runningConsumers = new Set(["defense-engine", "background-worker", "background-scheduler"]);
  const queuedFixture = { state: "queued" };
  const dispatchQueuedFixture = () => {
    if (runningConsumers.has("background-scheduler")) queuedFixture.state = "leased";
  };
  const measurementFailure = new Error("workload interrupted after measurement");
  await assert.rejects(executeIsolatedBackgroundWorkload({
    stopConsumers: () => {
      for (const consumer of backgroundWorkloadConsumers) runningConsumers.delete(consumer);
    },
    measureWorkload: () => {
      dispatchQueuedFixture();
      assert.equal(queuedFixture.state, "queued", "scheduler must not claim measurement-owned rows");
      throw measurementFailure;
    },
    restoreConsumers: () => {
      for (const consumer of backgroundWorkloadConsumers) runningConsumers.add(consumer);
    },
  }), error => error === measurementFailure);
  dispatchQueuedFixture();
  assert.equal(queuedFixture.state, "leased", "normal dispatch resumes after fixture cleanup");
  assert.equal(runningConsumers.size, 3);
});

for (const failWorkload of [false, true]) {
  test(`background workload isolates consumers and restores them after ${failWorkload ? "failure" : "success"}`, async () => {
    const { executeIsolatedBackgroundWorkload } = await import("../../scripts/postgres-test-plan.mjs");
    const events = [];
    const benchmarkFailure = new Error("benchmark failed");
    const result = executeIsolatedBackgroundWorkload({
      stopConsumers: async () => { events.push("consumers stopped and verified"); },
      measureWorkload: async () => {
        events.push("seed, measure, cleanup");
        if (failWorkload) throw benchmarkFailure;
      },
      restoreConsumers: async () => { events.push("consumers restored and ready"); },
    });
    if (failWorkload) await assert.rejects(result, (error) => error === benchmarkFailure);
    else await result;
    assert.deepEqual(events, ["consumers stopped and verified", "seed, measure, cleanup", "consumers restored and ready"]);
  });
}

test("background workload restores partially stopped consumers without masking the isolation failure", async () => {
  const { executeIsolatedBackgroundWorkload } = await import("../../scripts/postgres-test-plan.mjs");
  const events = [];
  const isolationFailure = new Error("consumer stop failed");
  await assert.rejects(executeIsolatedBackgroundWorkload({
    stopConsumers: () => { events.push("stop"); throw isolationFailure; },
    measureWorkload: () => { events.push("benchmark"); },
    restoreConsumers: () => { events.push("restore"); },
  }), (error) => error === isolationFailure);
  assert.deepEqual(events, ["stop", "restore"]);
});

test("background workload preserves benchmark and consumer restoration failures", async () => {
  const { executeIsolatedBackgroundWorkload } = await import("../../scripts/postgres-test-plan.mjs");
  const benchmarkFailure = new Error("benchmark failed");
  const restorationFailure = new Error("consumer restoration failed");
  await assert.rejects(executeIsolatedBackgroundWorkload({
    stopConsumers: () => {},
    measureWorkload: () => { throw benchmarkFailure; },
    restoreConsumers: () => { throw restorationFailure; },
  }), (error) => error instanceof AggregateError
    && error.errors[0] === benchmarkFailure && error.errors[1] === restorationFailure);
});

test("issue80 durable application proofs cover recreation and restored pre/post activation state", () => {
  const source = readFileSync(join(root, "scripts/test-postgres-docker.mjs"), "utf8");
  const recreate = source.slice(source.indexOf('  command_recreation: async () => {'), source.indexOf('  backup_restore: async () => {'));
  assert(recreate.indexOf('runPrefixApplicationProof("--seed-retained")') < recreate.indexOf('...compose, "down"'));
  assert(recreate.indexOf('...compose, "up"') < recreate.indexOf('runPrefixApplicationProof("--verify-retained")'));
  const backup = source.slice(source.indexOf('  backup_restore: async () => {'), source.indexOf('  browser: async () => {'));
  assert(backup.indexOf('runPrefixApplicationProof("--seed-retained")') < backup.indexOf('pg_dump'));
  assert(backup.indexOf('verify_postgres_backup.py') < backup.indexOf('TEMPO_PREFIX_APPLICATION_PROOF_URL='));
  assert(backup.indexOf('TEMPO_PREFIX_APPLICATION_PROOF_URL=postgresql://postgres@postgres:5432/tempo_restore_check') < backup.indexOf('dropdb'));
  assert.match(backup, /runPrefixApplicationProof\("--recover-retained", "--cleanup-retained"\)/);
});
