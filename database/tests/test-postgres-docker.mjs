// Disposable PostgreSQL product and container-recreation gate.

import assert from "node:assert/strict";
import {allocatedNativeProofSource,postgresProofAllocations,verifyRestoredPostgresConsumers} from "./postgres-maintenance-proof.mjs";
import { withDevelopmentStartup, reconcileDevelopmentStartup, assertDevelopmentProjectOwned, composeResourceLimits, managedBuild, recordBudgetResource, runWithDevelopmentBudget, managedComposeBuild, resourceBudgets, waitForStudyQuiet } from '../../scripts/development-budget.mjs';
import { randomBytes } from "node:crypto";
import { spawnSync, execFileSync } from "node:child_process";
import { createServer } from "node:net";
import { accessSync, chmodSync, constants, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { createIsolatedTestEnvironment } from "../../scripts/test-environment.mjs";
import { resolvePython } from "../../scripts/resolve-python.mjs";
import { buildPostgresPlaywrightArguments, parsePostgresTestOptions } from "./postgres-test-options.mjs";
import { assertDisposableVolumes, verifyMaintainedQueueReceipt, verifyMaintainedBrowserReceipt, unresolvedQueueCases, backgroundWorkloadConsumers, executeDiagnosticCleanup, executeIsolatedBackgroundWorkload, executePostgresTestPlan, postgresTestStages, restoreBackgroundWorkloadConsumers } from "./postgres-test-plan.mjs";
import { assertNoCompletedFixtureConflict, backgroundPublicationPgn,
  readStudyQueuePublication, repertoireLimitRecreationPgn, studyDurabilityPgn } from "./postgres-test-fixture.mjs";
import { createScenarioTimer, isTimingPublicationFailure, postgresCommandTiming } from "../../scripts/test-scenario-timings.mjs";
import { checkLifecycleSource, readSchema62Bridge } from "../../scripts/lifecycle-source-preflight.mjs";
import { buildMaterials, normalizeCandidateBake, writeCandidateReceipt, verifyCandidateReceipt } from "../../scripts/release-candidate-artifacts.mjs";
import { inspectImmutableImageSet } from "../../scripts/tempo-runtime.mjs";
import { verifyTempoCliLifecycle } from "../../scripts/check-tempo-cli.mjs";
import { atomicJson, redact, schemaVersionFromSource } from "../../scripts/tempo-deployment.mjs";
import { prepareLifecycleImages } from "../../scripts/prepare-lifecycle-images.mjs";

const options = parsePostgresTestOptions(process.argv.slice(2));
const staleOnly = process.env.TEMPO_PG_STALE_DELIVERY_ONLY === "1";
const stages = staleOnly
  ? ["compose_config", "image_build", "maintenance_cli", "startup", "service_health", "stale_delivery", "cleanup"]
  : postgresTestStages(options);
if (options.list) {
  console.log(JSON.stringify({ mode: options.mode, stages,
    browser_file: options.browserFile, browser_grep: options.browserGrep,
    acceptance_scope: options.maintainedStudyProof ? "partial-maintained-study-not-full-E02" : options.mode,
    unresolved_owner_cases: options.maintainedStudyProof ? unresolvedQueueCases : [] }, null, 2));
  process.exit(0);
}

// Reject invalid opt-in inputs before budget admission or temporary secrets.
if (options.mode === "lifecycle-current") {
  if (staleOnly || process.env.TEMPO_LIFECYCLE_P0_59_ONLY === "1"
      || schemaVersionFromSource(readFileSync("database/tempo_storage/ddl/schema_version.py", "utf8")) !== 63)
    throw new Error("Current lifecycle profile requires schema 63 and the schema-61 predecessor fixture without other scenario overrides");
}
if (process.env.TEMPO_RELEASE_CANDIDATE_MODE) {
  const mode = process.env.TEMPO_RELEASE_CANDIDATE_MODE, owner = process.env.TEMPO_RELEASE_CANDIDATE_OWNER;
  const path = process.env.TEMPO_RELEASE_CANDIDATE_RECEIPT;
  if (!["produce", "borrow"].includes(mode) || (mode === "produce" && !["lifecycle", "lifecycle-current"].includes(options.mode))
      || !/^[-a-z0-9]{12,80}$/.test(owner ?? "") || !path || resolve(path) !== path)
    throw new Error("Candidate reuse requires produce or borrow, an absolute receipt path and unique owner");
  if (["DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"].some(name => process.env[name]))
    throw new Error("Candidate reuse requires a named Docker context without host or TLS overrides");
  const parent = dirname(path), stat = lstatSync(parent);
  if (!stat.isDirectory() || stat.mode & 0o077) throw new Error("Candidate receipt parent must be a private existing directory");
  accessSync(parent, constants.W_OK);
  if (mode === "produce" ? existsSync(path) : !existsSync(path) || !lstatSync(path).isFile())
    throw new Error("Candidate receipt destination is unavailable or already exists");
  for (const args of [["rev-parse", "HEAD"], ["rev-parse", "HEAD^{tree}"], ["--no-optional-locks", "status", "--porcelain", "--untracked-files=all"]]) {
    const result = spawnSync("git", args, { encoding: "utf8", timeout: 15_000 });
    if (result.error || result.status !== 0 || (args.includes("status") && result.stdout.trim()))
      throw new Error("Candidate release source must be clean and identifiable");
  }
}

if (stages.includes("deployment_lifecycle") || stages.includes("deployment_lifecycle_current")) {
  process.env.TEMPO_LIFECYCLE_SCHEMA62_IMAGES ??= resolve("config/lifecycle-images/schema62-linux-arm64.json");
  if (process.env.TEMPO_LIFECYCLE_P0_59_ONLY !== "1" && !process.env.TEMPO_LIFECYCLE_SCHEMA61_IMAGES
      && ["DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"].some(name => process.env[name]))
    throw new Error("Automatic historical image preparation does not support inherited Docker host or TLS overrides; supply an explicit historical manifest or use a named context.");
  checkLifecycleSource({ profile: options.mode === "lifecycle-current" ? "current63" : "full" });
}

// Docker Desktop only shares the project checkout, so the short-lived secret
if (!process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN)
  process.exit(await runWithDevelopmentBudget(process.execPath, process.argv.slice(1)));

// Docker Desktop only shares the project checkout, so the short-lived secret
// files must be created there and removed in the cleanup block.
const secretsDirectory = mkdtempSync(join(process.cwd(), ".tempo-pg-test-secrets-"));
chmodSync(secretsDirectory, 0o700);
const administratorPassword = randomBytes(24).toString("hex");
const readerPassword = randomBytes(24).toString("hex");
const writerPassword = randomBytes(24).toString("hex");
const secretFiles = {
  admin_password: `${administratorPassword}\n`,
  admin_pgpass: `postgres:5432:*:postgres:${administratorPassword}\n`,
  reader_password: `${readerPassword}\n`,
  writer_password: `${writerPassword}\n`,
  reader_pgpass: `postgres:5432:tempo:tempo_reader:${readerPassword}\n`,
  writer_pgpass: `postgres:5432:tempo:tempo_writer:${writerPassword}\n`,
};
for (const [name, value] of Object.entries(secretFiles)) {
  writeFileSync(join(secretsDirectory, name), value, { mode: 0o600 });
}
const testPort = await new Promise((resolve, reject) => {
  const server = createServer();
  server.once("error", reject);
  server.listen(0, "127.0.0.1", () => {
    const address = server.address();
    if (!address || typeof address === "string") return reject(new Error("Could not reserve a port"));
    server.close(() => resolve(address.port));
  });
});
const project = `tempo-pg-regressions-${process.pid}-${randomBytes(4).toString("hex")}`;
const maintenanceImage = `${project}-maintenance`;
const maintenanceHelpContainer = `${project}-maintenance-help`;
const compose = ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml"];
const resourceLimits = join(secretsDirectory, 'resource-limits.json');
writeFileSync(resourceLimits, JSON.stringify(composeResourceLimits(['postgres', 'redis', 'api',
  'foreground-worker', 'background-worker', 'background-scheduler', 'web', 'schema'],
  process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN)), { mode: 0o600 });
compose.push('-f', resourceLimits);
const priorityResourceLimits = join(secretsDirectory,'priority-resource-limits.json');
writeFileSync(priorityResourceLimits,JSON.stringify(composeResourceLimits(Object.keys(resourceBudgets.priorityRehearsalServices),
  process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN,resourceBudgets.priorityRehearsalServices)),{mode:0o600});
const diagnosticResourceLimits = join(secretsDirectory,'diagnostic-resource-limits.json');
writeFileSync(diagnosticResourceLimits,JSON.stringify(composeResourceLimits(['schema'],
  process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN,resourceBudgets.diagnosticRehearsalServices)),{mode:0o600});
const environment = createIsolatedTestEnvironment(process.env, {
  TEMPO_PG_TEST_SECRETS: secretsDirectory,
  TEMPO_PG_TEST_PORT: String(testPort),
  TEMPO_PG_MAINTENANCE_IMAGE: maintenanceImage,
  TEMPO_POSTGRES_ADMIN_PASSWORD_FILE: join(secretsDirectory, "admin_password"),
  TEMPO_POSTGRES_READER_PGPASS_FILE: join(secretsDirectory, "reader_pgpass"),
  TEMPO_POSTGRES_WRITER_PGPASS_FILE: join(secretsDirectory, "writer_pgpass"),
  TEMPO_POSTGRES_ADMIN_PGPASS_FILE: join(secretsDirectory, "admin_pgpass"),
});
const origin = `http://127.0.0.1:${testPort}`;
let resourcesCreated = false;
let maintenanceImageCleanupRequired = false;
const timingPath = join(process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance",
  `postgres-scenarios-${options.mode}-${project}.json`);
const commitResult = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
const candidateRevision = commitResult.stdout.trim();
const buildLabels = join(secretsDirectory, "image-labels.json");
writeFileSync(buildLabels, JSON.stringify({ services: Object.fromEntries([
  "schema", "api", "foreground-worker", "background-worker", "background-scheduler", "web",
].map(name => [name, { build: { labels: { "org.opencontainers.image.revision": candidateRevision } } }])) }));
compose.push("-f", buildLabels);
const measureScenario = createScenarioTimer(timingPath, {
  runner: "postgres", mode: options.mode,
  commit: commitResult.status === 0 ? commitResult.stdout.trim() : null,
  plan_hash: process.env.TEMPO_CI_PLAN_HASH ?? null,
  acceptance_scope: options.maintainedStudyProof ? "partial-maintained-study-not-full-E02" : options.mode,
  native_checks: options.nativeChecks ?? [],
  browser_file: options.browserFile, browser_grep: options.browserGrep,
  planned_stages: stages,
});
console.log(`PostgreSQL ${options.mode} timings: ${timingPath}`);
const measuredHttpRequests = [];
let activeStudyRepertoireId = null;
let limitRecreationRepertoireId = null;

async function apiRequest(path, options = {}, label = null) {
  const startedAt = performance.now();
  const response = await fetch(`${origin}/api/${path}`, { signal: AbortSignal.timeout(15_000), ...options });
  if (label) measuredHttpRequests.push({ label, durationMs: performance.now() - startedAt });
  return response;
}

async function verifyStudyOpeningAdmission() {
  const nativeProofPath = "/source/database/tests/check_postgres_study_opening_admission.py";
  run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python", nativeProofPath]);
  const restartDatabaseName = `tempo_study_intros_restart_${randomBytes(16).toString("hex")}`;
  run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python", nativeProofPath,
    "--restart-stage", "prepare", "--restart-database", restartDatabaseName]);
  // Even though the proof database is private, restarting the shared test
  // server invalidates API/worker pools. Quiesce the invocation's clients and
  // restore fresh pools before subsequent foreground durability assertions.
  const postgresClients = ["api", "foreground-worker", "background-worker", "background-scheduler"];
  await executeIsolatedBackgroundWorkload({
    stopConsumers: () => run("docker", [...compose, "stop", ...postgresClients]),
    measureWorkload: () => run("docker", [...compose, "restart", "postgres"]),
    restoreConsumers: async () => {
      run("docker", [...compose, "up", "--no-build", "-d", "--no-deps", ...postgresClients]);
      // Nginx resolves its API upstream at startup; restored containers may
      // receive different addresses even though their service names persist.
      run("docker", [...compose, "restart", "web"]);
      await waitForReady();
      run("docker", [...compose, "up", "--no-build", "-d", "--no-deps"]);
    },
  });
  run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python", nativeProofPath,
    "--restart-stage", "verify", "--restart-database", restartDatabaseName]);
}

async function withPriorityMaintenanceAllocation(proof) {
  const clients=['api','foreground-worker','background-worker','background-scheduler','web'];
  await executeIsolatedBackgroundWorkload({
    stopConsumers: () => run('docker',[...compose,'stop',...clients]),
    measureWorkload: proof,
    restoreConsumers: async () => {
      run('docker',[...compose,'up','--no-build','-d']);
      await waitForReady();
      verifyRestoredPostgresConsumers(run,compose,clients);
    },
  });
}

async function runAllocatedNativeProof(scriptPath, proofName, containerOptions = []) {
    const limit=resourceBudgets.priorityRehearsalServices.schema;
    const proof=allocatedNativeProofSource(scriptPath,proofName,limit);
    await withPriorityMaintenanceAllocation(() => run("docker", [...compose, "-f", priorityResourceLimits,
      "run", "--rm", "--no-deps", ...containerOptions, "schema", "python", "-c", proof]));
}

async function verifyStudyPriorityMaintenance(currentOnly = false) {
  await withPriorityMaintenanceAllocation(async () => {
    await measureScenario.detail('study_priority_read_only_source_evidence', 'scenario', async () => {
      const evidenceParent = join(process.env.TEMPO_TEST_TIMING_DIR ?? 'test-results/performance', 'study-priority');
      mkdirSync(evidenceParent, {recursive: true});
      const evidenceDirectory = resolve(evidenceParent, project);
      mkdirSync(evidenceDirectory, {mode: 0o700});
      run('docker',[...compose,'-f',priorityResourceLimits,'run','--rm','--no-deps',
        '--volume', `${evidenceDirectory}:/evidence`, 'schema','python',
        '/source/database/tests/check_postgres_study_priority.py','--owned-stack','--evidence','/evidence',
        ...(currentOnly ? ['--current-only'] : [])]);
      const receipt = JSON.parse(readFileSync(join(evidenceDirectory, 'native.json'), 'utf8'));
      assert.equal(receipt.complete, true, 'Read-only-source priority proof did not retain complete evidence');
      assert.equal(Object.keys(receipt.cases).length, currentOnly ? 5 : 6, 'Priority proof omitted native cases');
      if (currentOnly) assert.equal(receipt.current_only, true);
      atomicJson(join(evidenceDirectory, 'assembly.json'), {revision: candidateRevision, project,
        source_mount: 'read-only', evidence_mount: '/evidence', complete: true,
        scope: currentOnly ? 'current-five-historical-rollback-unexecuted' : 'all-six'});
    });
  });
}

async function verifyStudyQueueReceipts(maintained = false) {
    await executeIsolatedBackgroundWorkload({
      stopConsumers: () => {
        run("docker", [...compose, "stop", ...workloadConsumers]);
        verifyWorkloadConsumers("exited");
      },
      measureWorkload: () => {
        const result = run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/database/tests/check_postgres_queue_attempt_recovery.py", ...(maintained ? ["--case-set", "maintained-six"] : [])],
          {stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8', maxBuffer: 16 * 1024 * 1024, echoCapturedOutput: true});
        if (!maintained) return;
        const records = result.stdout.split('\n').filter(line => line.startsWith('{')).map(line => JSON.parse(line));
        const receipt = records.findLast(record => record.execution === 'completed');
        verifyMaintainedQueueReceipt(receipt);
        atomicJson(`test-results/native/${project}-maintained-queue.json`, {revision: candidateRevision, project, ...receipt});
      },
      restoreConsumers: () => restoreBackgroundWorkloadConsumers({
        startConsumers: services => run("docker", [...compose, "up", "--no-build", "-d", "--no-deps", ...services]),
        waitForReady: () => waitForReady({requireContainerHealthy: true}), verifyConsumers: verifyWorkloadConsumers,
      }),
    });
}

function verifyDisposableVolumesAbsent(names) {
  for (const name of names) {
    const result = spawnSync("docker", ["volume", "inspect", name], { encoding: "utf8", env: environment });
    if (result.error || result.status === 0 || !/no such volume/i.test(result.stderr ?? ""))
      throw new Error(`Disposable volume absence is unverified: ${name}`);
  }
}

function verifyProjectIsUnused() {
  for (const [resourceName, argumentsList] of [
    ["container", ["ps", "-aq", "--filter", `label=com.docker.compose.project=${project}`]],
    ["volume", ["volume", "ls", "-q", "--filter", `label=com.docker.compose.project=${project}`]],
    ["network", ["network", "ls", "-q", "--filter", `label=com.docker.compose.project=${project}`]],
  ]) {
    const result = spawnSync("docker", argumentsList, { encoding: "utf8", env: environment });
    assert.equal(result.status, 0, `Could not verify test ${resourceName} isolation`);
    assert.equal(result.stdout.trim(), "", `Refusing to reuse pre-existing test ${resourceName}s`);
  }
}

function isMissingMaintenanceImage(result) {
  return !result.error && result.status === 1
    && [maintenanceImage, `${maintenanceImage}:latest`].some(tag =>
      result.stderr?.trim() === `Error response from daemon: No such image: ${tag}`);
}

function removeOwnedMaintenanceHelpContainer() {
  const inspect = identity => {
    const result = spawnSync('docker',['container','inspect',identity],{encoding:'utf8',env:environment,timeout:5000});
    if(!result.error && result.status===1 && result.stderr?.trim()===`Error response from daemon: No such container: ${identity}`)return undefined;
    assert.equal(result.status,0,'Maintenance help container inspection is uncertain');
    const values=JSON.parse(result.stdout);assert.equal(values.length,1);return values[0];
  };
  const container=inspect(maintenanceHelpContainer);if(!container)return;
  assert.equal(container.Name,'/'+maintenanceHelpContainer);
  assert.equal(container.Config.Labels['tempo.resource-owner'],process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN,'Maintenance help ownership mismatch');
  assert.equal(container.Config.Labels['com.docker.compose.project'],project,'Maintenance help project mismatch');
  const removed=spawnSync('docker',['rm','--force','--volumes',container.Id],{encoding:'utf8',env:environment,timeout:5000});
  assert(!removed.error&&removed.status===0,'Maintenance help container removal failed');
  assert.equal(inspect(maintenanceHelpContainer),undefined,'Maintenance help name still exists after removal');
  assert.equal(inspect(container.Id),undefined,'Maintenance help identity still exists after removal');
}

function run(command, argumentsList, options = {}) {
  const { echoCapturedOutput = false, ...spawnOptions } = options;
  const { label, category } = postgresCommandTiming(argumentsList);
  const execute = () => measureScenario.syncDetail(label, category, () => {
    const result = spawnSync(command, argumentsList, { stdio: "inherit", env: environment, ...spawnOptions });
    if (echoCapturedOutput && result.stdout) process.stdout.write(result.stdout);
    measureScenario.processResult(result);
    if (result.error || result.status !== 0) throw new Error(`${command} ${argumentsList.join(" ")} failed`);
    return result;
  });
  if(command==='docker'&&argumentsList[0]==='run') {
    for(const value of [maintenanceHelpContainer,`tempo.resource-owner=${process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN}`,
      `com.docker.compose.project=${project}`,'com.docker.compose.service=maintenance-help'])assert(argumentsList.includes(value),'Untracked standalone maintenance startup');
    assert(!argumentsList.includes('--cpus'));
    assert.equal(argumentsList[argumentsList.indexOf('--memory')+1],'256m');
    return withDevelopmentStartup(['maintenance-help'],project,execute,{oneOff:true,allocations:{'maintenance-help':{memoryMiB:256}}});
  }
  if (command === 'docker' && ['up', 'start', 'run'].some(operation => argumentsList.includes(operation))) {
    const names = ['postgres', 'redis', 'api', 'foreground-worker', 'background-worker',
      'background-scheduler', 'web', 'schema'];
    const requested = names.filter(name => argumentsList.includes(name));
    return withDevelopmentStartup(requested.length ? requested : names.filter(name => name !== 'schema'),project,execute,
      {oneOff:argumentsList.includes('run'),allocations:postgresProofAllocations(argumentsList,
        {priority:priorityResourceLimits,diagnostic:diagnosticResourceLimits},resourceBudgets)});
  }
  return execute();
}

const workloadConsumers = backgroundWorkloadConsumers;

function verifyWorkloadConsumers(expectedState) {
  const result = spawnSync("docker", [...compose, "ps", "--all", "--format", "json", ...workloadConsumers],
    { encoding: "utf8", env: environment });
  assert.equal(result.status, 0, result.stderr);
  const states = new Map(result.stdout.trim().split("\n").filter(Boolean)
    .map(line => JSON.parse(line)).map(container => [container.Service, container.State]));
  for (const service of workloadConsumers)
    assert.equal(states.get(service), expectedState, `${service} must be ${expectedState} for the workload benchmark`);
}

async function waitForReady(...argumentsList) {
  return measureScenario.detail("api_ready", "readiness", () => pollForReady(...argumentsList));
}

async function pollForReady({ requireContainerHealthy = false } = {}) {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    measureScenario.poll();
    try {
      const response = await fetch(`${origin}/api/health`, { signal: AbortSignal.timeout(3_000) });
      if (response.ok) {
        const health = await response.json();
        assert.equal(health.storage, "postgresql");
        assert.equal(health.test_instance, true);
        if (requireContainerHealthy) {
          const status = spawnSync("docker", [...compose, "ps", "--format", "json", "api"],
            { encoding: "utf8", env: environment });
          assert.equal(status.status, 0, "Could not inspect API container readiness");
          const containers = status.stdout.trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
          if (!containers.some(container => container.Service === "api" && container.Health === "healthy")) {
            await measureScenario.wait(1_000);
            continue;
          }
        }
        return;
      }
    } catch (error) {
      if (isTimingPublicationFailure(error)) throw error;
      // Ordinary connection failures remain bounded startup retries.
    }
    await measureScenario.wait(1_000);
  }
  throw new Error("Disposable PostgreSQL Tempo did not become ready");
}

async function get(path, { background = false } = {}) {
  const response = await apiRequest(path, background ? { headers: { "X-Tempo-Work-Class": "background" } } : {});
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status} ${await response.text()}`);
  return response.json();
}

async function postCommand(path, payload, { method = "POST", operationId, label } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (operationId) headers["Idempotency-Key"] = operationId;
  return confirm(await apiRequest(path, {
    method, headers, body: JSON.stringify(payload),
  }, label));
}

async function confirm(...argumentsList) {
  return measureScenario.detail("operation_receipt", "readiness", () => pollForConfirmation(...argumentsList));
}

async function pollForConfirmation(response) {
  if (response.status !== 202) {
    if (!response.ok) throw new Error(`command HTTP ${response.status} ${await response.text()}`);
    return response.json();
  }
  const pending = await response.json();
  assert(pending.operation_id, "202 command must return an operation ID");
  for (let attempt = 0; attempt < 120; attempt += 1) {
    measureScenario.poll();
    const receipt = await get(`operations/${encodeURIComponent(pending.operation_id)}`);
    if (receipt.state === "complete") return receipt.response;
    if (receipt.state === "failed") throw new Error(`PostgreSQL command failed: ${JSON.stringify(receipt.error)}`);
    await measureScenario.wait(250);
  }
  throw new Error(`PostgreSQL operation ${pending.operation_id} remains pending`);
}

async function waitForStudyableImport(...argumentsList) {
  return measureScenario.detail("import_publication", "readiness", () => pollForStudyableImport(...argumentsList));
}

async function pollForStudyableImport(repertoireId) {
  let settledSamples = 0;
  for (let attempt = 0; attempt < 180; attempt += 1) {
    measureScenario.poll();
    const [system, integrity, queue] = await Promise.all([
      get("system/tasks", { background: true }), get(`repertoires/${repertoireId}/integrity`, { background: true }),
      apiRequest("queue/today", { headers: { "X-Tempo-Work-Class": "background" } }).then(readStudyQueuePublication),
    ]);
    assertNoCompletedFixtureConflict(integrity, repertoireId);
    const graphTask = system.tasks.find((task) => task.kind === "opening_graph_rebuild"
      && task.deduplication_key === repertoireId);
    if (graphTask?.state === "failed" || integrity.scan_status === "failed"
      || queue?.projection?.state === "failed") {
      throw new Error(graphTask?.last_error ?? integrity.last_scan_error ?? integrity.last_error
        ?? queue?.projection?.last_error ?? "PostgreSQL import background work failed");
    }
    if (graphTask?.state === "complete" && integrity.scan_status === "idle" && integrity.status === "clean"
      && queue?.projection?.state === "ready" && !queue.projection.refresh_pending) {
      settledSamples += 1;
      if (settledSamples >= 3) return queue;
    } else {
      settledSamples = 0;
    }
    await measureScenario.wait(250);
  }
  throw new Error(`PostgreSQL graph, integrity, and queue work did not settle for ${repertoireId}`);
}

function stableStudyState(snapshot, repertoireId) {
  const tables = snapshot.tables;
  const repertoireCards = tables.repertoire_cards
    .filter((row) => row.repertoire_id === repertoireId)
    .map((row) => ({ repertoire_id: row.repertoire_id, card_id: row.card_id }))
    .sort((left, right) => left.card_id.localeCompare(right.card_id));
  const cardIds = new Set(repertoireCards.map((row) => row.card_id));
  for (const card of tables.cards) {
    if (card.repertoire_id === repertoireId) cardIds.add(card.id);
  }
  const selectRows = (tableName, predicate, fields) => tables[tableName]
    .filter(predicate)
    .map((row) => Object.fromEntries(fields.filter((field) => field in row)
      .map((field) => [field, row[field]])))
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
  return {
    repertoire: selectRows("repertoires", (row) => row.id === repertoireId,
      ["id", "name", "source_name"]),
    repertoireCards,
    cards: selectRows("cards", (row) => cardIds.has(row.id), [
      "id", "repertoire_id", "kind", "start_fen", "moves_json", "state", "due_date",
      "interval_days", "repetitions", "lapses", "fsrs_card_json", "reinforcement_pending",
      "stability", "guided_review", "revision",
    ]),
    reviews: selectRows("reviews", (row) => cardIds.has(row.card_id), [
      "id", "card_id", "rating", "reviewed_at", "previous_interval", "next_interval",
      "internal_rating", "guided",
    ]),
    teaching: selectRows("teaching_states", (row) => cardIds.has(row.card_id),
      ["card_id", "revision", "ply", "taught_at"]),
    annotations: selectRows("position_annotations", (row) => row.repertoire_id === repertoireId,
      ["repertoire_id", "fen_key", "comment", "arrows_json", "squares_json"]),
    queue: selectRows("daily_queue", (row) => cardIds.has(row.card_id), [
      "id", "queue_date", "card_id", "cycle", "position", "status", "attempt_state",
      "attempt_failed", "review_result_json",
    ]),
    queueOrigins: selectRows("queue_attempt_origins", (row) => cardIds.has(row.card_id), [
      "queue_entry_id", "card_id", "revision", "queue_date", "cycle", "admission_kind",
      "admission_repertoire_id", "attempt_failed", "last_status", "review_result_json",
      "legacy", "start_fen", "moves_json", "trained_color", "content_type",
    ]),
    attemptReceipts: selectRows("review_attempt_receipts", (row) => cardIds.has(row.card_id), [
      "attempt_id", "card_id", "queue_entry_id", "outcome", "guided", "completed_at",
      "review_id", "scheduling_status", "warning", "result_json", "request_json",
    ]),
    prefixSplits: selectRows("prefix_splits", (row) => cardIds.has(row.source_card_id), [
      "source_card_id", "source_revision", "shortened_card_id", "continuation_card_id",
    ]),
  };
}

async function importFixture(...argumentsList) {
  return measureScenario.detail("pgn_fixture", "fixture", () => submitPgnFixture(...argumentsList));
}

async function submitPgnFixture(sourceName, pgn, operationId) {
  const form = new FormData();
  form.set("file", new Blob([pgn], { type: "application/x-chess-pgn" }), sourceName);
  form.set("trained_color", "white");
  form.set("initial_depth", "6");
  const imported = await confirm(await apiRequest("imports/pgn", {
    method: "POST", body: form, ...(operationId ? { headers: { "Idempotency-Key": operationId } } : {}),
  },
    `foreground POST imports/pgn (${sourceName})`));
  assert(imported.repertoire_id, "PGN import returns the persisted repertoire identity");
  return imported;
}

async function verifyPgnImportReplayWithoutDuplicates(operationId, originalResult, phase) {
  const before = await readStudySnapshot();
  const beforeReceipt = await get(`operations/${operationId}`);
  assert.equal(beforeReceipt.state, "complete");
  const replay = await importFixture("recovery-study.pgn", studyDurabilityPgn, operationId);
  assert.deepEqual(replay, originalResult, "PGN replay returns the original logical command result");
  const after = await readStudySnapshot();
  const importIdentities = (snapshot) => {
    const tables = snapshot.tables;
    const links = tables.repertoire_cards.filter(row => row.repertoire_id === originalResult.repertoire_id);
    const cardIds = new Set(links.map(row => row.card_id));
    return {
      repertoires: tables.repertoires.filter(row => row.source_name === "recovery-study.pgn").map(row => row.id).sort(),
      lines: tables.repertoire_lines.filter(row => row.repertoire_id === originalResult.repertoire_id)
        .map(row => [row.id, row.start_fen, row.moves_json]).sort(),
      links: links.map(row => row.card_id).sort(),
      cards: tables.cards.filter(row => cardIds.has(row.id) || row.repertoire_id === originalResult.repertoire_id)
        .map(row => row.id).sort(),
    };
  };
  assert(importIdentities(before).lines.length > 0 && importIdentities(before).cards.length > 0,
    "Replay proof includes persisted source lines and materialized cards");
  assert.deepEqual(importIdentities(after), importIdentities(before),
    "PGN replay cannot duplicate repertoire, lines, card links, or cards");
  assert.deepEqual(await get(`operations/${operationId}`), beforeReceipt,
    "Completed PGN replay cannot execute another handler attempt");
  console.log(`PASS verifyPgnImportReplayWithoutDuplicates ${phase}: one receipt/result and unchanged lines/cards`);
}

async function waitForStudyQueue(...argumentsList) {
  return measureScenario.detail("study_queue", "readiness", () => pollForStudyQueue(...argumentsList));
}

async function pollForStudyQueue(repertoireId, minimumCardCount) {
  const initialSnapshot = await readStudySnapshot();
  const repertoireCardIds = new Set((initialSnapshot.tables.repertoire_cards ?? [])
    .filter((row) => row.repertoire_id === repertoireId)
    .map((row) => row.card_id));
  for (const card of initialSnapshot.tables.cards ?? []) {
    if (card.repertoire_id === repertoireId) repertoireCardIds.add(card.id);
  }
  for (let attempt = 0; attempt < 240; attempt += 1) {
    measureScenario.poll();
    const [queue, integrity] = await Promise.all([
      get("queue/today"), get(`repertoires/${repertoireId}/integrity`),
    ]);
    assertNoCompletedFixtureConflict(integrity, repertoireId);
    const cards = queue.cards.filter((card) => repertoireCardIds.has(card.id));
    if (cards.length >= minimumCardCount) return { queue, snapshot: initialSnapshot, cards };
    if (queue.projection?.state === "failed")
      throw new Error(queue.projection.last_error ?? "PostgreSQL study queue projection failed");
    await measureScenario.wait(250);
  }
  const queue = await get("queue/today");
  throw new Error(`PostgreSQL study queue did not admit ${minimumCardCount} entries for ${repertoireId}; `
    + `queue=${queue.cards.filter((card) => repertoireCardIds.has(card.id)).length}, `
    + `projection=${JSON.stringify(queue.projection ?? null)}`);
}

// Exact retained observation rows from this invocation's disposable authority.
// This replaces only the retired snapshot transport, not the proof assertions.
async function readStudySnapshot() {
  assertDevelopmentProjectOwned(project);
  assert.match(project, /^tempo-pg-regressions-\d+-[a-f0-9]+$/);
  const health = await get("health");
  assert.equal(health.test_instance, true);
  assert.equal(health.storage, "postgresql");
  const address = new URL(origin);
  assert.equal(address.hostname, "127.0.0.1");
  const metadata = run("docker", ["inspect", "--format",
    '{"id":{{json .Id}},"labels":{{json .Config.Labels}},"ports":{{json .NetworkSettings.Ports}}}',
    `${project}-web-1`, `${project}-postgres-1`],
    {stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8'}).stdout.trim().split('\n').map(line => JSON.parse(line));
  assert.equal(metadata.length, 2);
  for (const [index, container] of metadata.entries()) {
    assert.match(container.id, /^[a-f0-9]{64}$/);
    assert.equal(container.labels['com.docker.compose.project'], project);
    assert.equal(container.labels['com.docker.compose.service'], ['web', 'postgres'][index]);
    assert.equal(container.labels['tempo.resource-owner'], process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN);
  }
  assert.deepEqual(metadata[0].ports['80/tcp'], [{HostIp: '127.0.0.1', HostPort: address.port}]);
  const schema = schemaVersionFromSource(readFileSync("database/tempo_storage/ddl/schema_version.py", "utf8"));
  const tables = ['repertoires', 'repertoire_cards', 'repertoire_lines', 'cards', 'reviews',
    'teaching_states', 'position_annotations', 'daily_queue', 'queue_attempt_origins',
    'review_attempt_receipts', 'prefix_splits'];
  const statement = `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
    DO $$ BEGIN
      IF current_database() != 'tempo' OR
         (SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database())
           IS DISTINCT FROM 'tempo-disposable-postgres-test' THEN RAISE EXCEPTION 'Unowned snapshot database'; END IF;
      IF (SELECT array_agg(version ORDER BY version) FROM tempo_schema_migrations)
         IS DISTINCT FROM ARRAY(SELECT generate_series(1,${schema})) THEN RAISE EXCEPTION 'Snapshot schema mismatch'; END IF;
    END $$;
    SELECT json_build_object('tables',json_build_object(${tables.map(table =>
      `'${table}',(SELECT COALESCE(json_agg(row_to_json(rows) ORDER BY row_to_json(rows)::text),'[]'::json) FROM ${table} rows)`).join(',')}));
    COMMIT;`;
  const result = run("docker", ["exec", "--interactive=false", "-e",
    "PGOPTIONS=-c statement_timeout=10000 -c lock_timeout=1000", metadata[1].id,
    "psql", "-X", "-w", "-h", "/var/run/postgresql", "-U", "postgres", "-d", "tempo",
    "-v", "ON_ERROR_STOP=1", "-Atqc", statement],
    {stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8', maxBuffer: 16 * 1024 * 1024});
  const snapshot = JSON.parse(result.stdout.trim());
  assert.deepEqual(Object.keys(snapshot.tables), tables);
  for (const table of tables) assert(Array.isArray(snapshot.tables[table]));
  return snapshot;
}

function readScopedPostgresRows(statement) {
  const result = spawnSync("docker", [...compose, "exec", "-T", "postgres", "psql", "-U", "postgres",
    "-d", "tempo", "-Atqc", statement], { encoding: "utf8", env: environment });
  if (result.status !== 0) throw new Error(result.stderr);
  return JSON.parse(result.stdout.trim());
}

async function reportStudyFixtureDiagnostics(repertoireId) {
  assert.match(repertoireId, /^[0-9a-f-]{36}$/i);
  const [snapshot, integrity, system, queue] = await Promise.all([
    readStudySnapshot(), get(`repertoires/${repertoireId}/integrity`),
    get("system/tasks"), get("queue/today"),
  ]);
  const repertoireCardIds = new Set(snapshot.tables.repertoire_cards
    .filter((row) => row.repertoire_id === repertoireId).map((row) => row.card_id));
  for (const card of snapshot.tables.cards) {
    if (card.repertoire_id === repertoireId) repertoireCardIds.add(card.id);
  }
  const scopedSql = (table, fields, where) => readScopedPostgresRows(
    `SELECT COALESCE(jsonb_agg(to_jsonb(rows)), '[]'::jsonb) FROM `
    + `(SELECT ${fields} FROM ${table} WHERE ${where}) rows;`);
  const scopedCardWhere = `card_id IN (SELECT card_id FROM repertoire_cards WHERE repertoire_id='${repertoireId}' `
    + `UNION SELECT id FROM cards WHERE repertoire_id='${repertoireId}')`;
  const diagnostics = {
    repertoire_id: repertoireId,
    integrity,
    graph_tasks: system.tasks.filter((task) => task.deduplication_key === repertoireId),
    card_ids: [...repertoireCardIds].sort(),
    cards: snapshot.tables.cards.filter((card) => repertoireCardIds.has(card.id)).map((card) => ({
      id: card.id, archived: card.archived, pending_validation: card.pending_validation,
      state: card.state, revision: card.revision, move_count: JSON.parse(card.moves_json).length,
    })),
    queue_projection: queue.projection,
    queue_api_cards: queue.cards.filter((card) => repertoireCardIds.has(card.id)).map((card) => ({
      id: card.id, queue_entry_id: card.queue_entry_id, attempt_state: card.attempt_state,
    })),
    integrity_issues: scopedSql("repertoire_integrity_issues", "id,kind,fen_key,moves_json", `repertoire_id='${repertoireId}'`),
    integrity_card_blocks: scopedSql("repertoire_integrity_card_blocks", "card_id,issue_id,scan_generation", `repertoire_id='${repertoireId}'`),
    priority_jobs: scopedSql("repertoire_priority_jobs", "repertoire_id,generation,status,last_error", `repertoire_id='${repertoireId}'`),
    priority_publications: scopedSql("repertoire_priority_publications", "repertoire_id,generation", `repertoire_id='${repertoireId}'`),
    daily_queue: scopedSql("daily_queue", "id,queue_date,card_id,status,cycle,position,attempt_state,attempt_failed",
      `queue_date=CURRENT_DATE::text AND ${scopedCardWhere}`),
    queue_projection_rows: scopedSql("queue_projections", "queue_date,state,generation,refresh_pending,last_error",
      "queue_date=CURRENT_DATE::text"),
    operation_receipts: scopedSql("operation_receipts", "operation_id,command_name,state,error_json",
      `operation_id LIKE 'pg-study-%' OR response_json LIKE '%${repertoireId}%'`),
  };
  console.error(`PostgreSQL study fixture diagnostics: ${JSON.stringify(diagnostics)}`);
}

async function verifyDiscardedPgnReplay(operationId) {
  const receipt = await get(`operations/${operationId}`);
  assert.equal(receipt.state, "failed");
  assert.equal(receipt.error?.code, "import_discarded");
  const form = new FormData();
  form.set("file", new Blob(["1. e4 e5 2. Nf3 *"], { type: "application/x-chess-pgn" }), `${operationId}.pgn`);
  form.set("trained_color", "white");
  form.set("initial_depth", "6");
  const replay = await apiRequest("imports/pgn", { method: "POST", body: form,
    headers: { "Idempotency-Key": operationId } });
  assert.equal(replay.status, 409, `Discard fence rejects delayed PGN admission: ${await replay.text()}`);
  const snapshot = await readStudySnapshot();
  assert.equal(snapshot.tables.repertoires.filter(row => row.source_name === `${operationId}.pgn`).length, 0,
    "Discarded PGN cannot create repertoire data before or after recreation");
}

async function verifyForegroundAndStudyDurability() {
  const discardedPgnId = `pg-study-discard-${randomBytes(12).toString("hex")}`;
  const discarded = await confirm(await apiRequest(`imports/pgn/${discardedPgnId}/discard`, { method: "POST" }));
  assert.equal(discarded.outcome, "discarded");
  await verifyDiscardedPgnReplay(discardedPgnId);
  const studySettings = await get("settings");
  await postCommand("settings", { ...studySettings, new_cards_per_day: 100, study_new_per_day: 100 }, {
    method: "PUT", label: "foreground PUT study queue allowance",
  });
  const pgnImportOperationId = `pg-study-pgn-${randomBytes(12).toString("hex")}`;
  const importedStudy = await importFixture("recovery-study.pgn", studyDurabilityPgn, pgnImportOperationId);
  activeStudyRepertoireId = importedStudy.repertoire_id;
  await postCommand("settings", await get("settings"), {
    method: "PUT", label: "foreground PUT initial study queue refresh",
  });
  await waitForStudyableImport(importedStudy.repertoire_id);
  await verifyPgnImportReplayWithoutDuplicates(pgnImportOperationId, importedStudy, "before recreation");
  const { cards: studyCards } = await waitForStudyQueue(importedStudy.repertoire_id, 3);
  const reviewCard = studyCards[0];
  const guidedCard = studyCards[1];
  const splitCard = studyCards.find((card) => card.id !== reviewCard.id
    && card.id !== guidedCard.id && card.moves.length >= 3);
  assert(splitCard, "Study fixture includes a third multi-move prefix-split entry");

  run("docker", [...compose, "stop", "background-worker"]);
  const evidenceQueue = await get("queue/today?include_opening_evidence=true");
  const evidenceCard = evidenceQueue.cards.find(card => card.queue_entry_id === reviewCard.queue_entry_id);
  assert(evidenceCard?.opening_decision_manifest, "Foreground fixture has authoritative evidence");
  const manifest = evidenceCard.opening_decision_manifest;
  const observedAt = new Date().toISOString();
  const checkpointOperationId = `pg-background-checkpoint-${randomBytes(12).toString("hex")}`;
  const backgroundCheckpoint = {
    attempt_id: `pg-background-attempt-${randomBytes(12).toString("hex")}`, manifest,
    origin_queue_entry_id: evidenceCard.queue_entry_id, queue_entry_id: evidenceCard.queue_entry_id,
    started_at: observedAt, study_timezone: "UTC", source: "live",
    events: [{ sequence: 1, decision_index: 0, decision_id: manifest.decisions[0].decision_id,
      expected_uci: manifest.decisions[0].expected_uci, response_uci: manifest.decisions[0].expected_uci,
      observed_at: observedAt, kind: "first_response", disposition: "expected" }],
    terminal: { state: "partial", final_sequence: 1, ended_at: observedAt },
  };
  const queuedCheckpoint = await apiRequest("opening-evidence/checkpoints", {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": checkpointOperationId },
    body: JSON.stringify(backgroundCheckpoint),
  });
  assert.equal(queuedCheckpoint.status, 202, "A stopped background worker cannot execute the checkpoint on foreground");
  assert.equal((await queuedCheckpoint.json()).operation_id, checkpointOperationId);
  const importedBackground = await importFixture("background-publication.pgn", backgroundPublicationPgn);
  const queuedSystem = await get("system/tasks");
  assert(queuedSystem.tasks.some((task) => task.kind === "opening_graph_rebuild"
    && task.deduplication_key === importedBackground.repertoire_id
    && ["queued", "retrying"].includes(task.state)),
  "Background graph publication remains durably queued while its worker is stopped");

  const teaching = await postCommand(`cards/${reviewCard.id}/teaching`, {
    revision: reviewCard.revision, ply: 0,
  }, { label: "foreground POST card teaching" });
  assert.equal(teaching.cardId, reviewCard.id);
  const annotation = await postCommand(`repertoires/${importedStudy.repertoire_id}/annotations`, {
    fen: reviewCard.start_fen,
    comment: "Persisted PostgreSQL recovery annotation",
    arrows: [{ from: "e2", to: "e4", color: "green" }],
    squares: [{ square: "d4", color: "red" }],
  }, { method: "PUT", label: "foreground PUT annotations" });
  assert.equal(annotation.comment, "Persisted PostgreSQL recovery annotation");

  const reviewOperationId = `pg-study-review-${randomBytes(12).toString("hex")}`;
  const reviewBody = {
    outcome: "correct", queue_entry_id: reviewCard.queue_entry_id,
    expected_revision: reviewCard.revision,
    attempt_id: `pg-study-attempt-${randomBytes(10).toString("hex")}`,
  };
  await postCommand(`cards/${reviewCard.id}/review`, reviewBody, {
    operationId: reviewOperationId, label: "foreground POST review",
  });
  const failedAttempt = await postCommand(`queue/entries/${guidedCard.queue_entry_id}/fail`, {}, {
    label: "foreground POST guided failure",
  });
  assert(failedAttempt, "Guided failure command receives a confirmed response");

  const splitPreview = await get(`cards/${splitCard.id}/prefix-split`);
  const split = await postCommand(`cards/${splitCard.id}/prefix-split`, {
    expected_revision: splitPreview.source_revision,
  }, {
    operationId: `pg-study-prefix-split-${randomBytes(10).toString("hex")}`,
    label: "foreground POST prefix split",
  });
  assert.equal(split.applied, true);
  const queueRead = await apiRequest("queue/today", {}, "foreground GET queue/today during background backlog");
  assert.equal(queueRead.status, 200);
  const liveQueue = await queueRead.json();
  assert(liveQueue.cards.length > 0, "Foreground queue read remains available during background backlog");

  console.log(`PASS PostgreSQL foreground actions committed with queued background work: ${JSON.stringify(measuredHttpRequests.map(({ label, durationMs }) => ({ label, milliseconds: Math.round(durationMs * 10) / 10 })))}`);
  const sqlStartedAt = performance.now();
  run("docker", [...compose, "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo",
    "-Atqc", "SELECT count(*) FROM reviews; SELECT count(*) FROM queue_projections;"]);
  console.log(`PostgreSQL direct SQL probe: ${Math.round((performance.now() - sqlStartedAt) * 10) / 10}ms (review and queue-projection counts)`);
  run("docker", [...compose, "start", "background-worker"]);
  const persistedCheckpoint = await postCommand("opening-evidence/checkpoints", backgroundCheckpoint,
    { operationId: checkpointOperationId });
  assert.equal(persistedCheckpoint.persisted, true);
  assert.deepEqual(await postCommand("opening-evidence/checkpoints", backgroundCheckpoint,
    { operationId: checkpointOperationId }), persistedCheckpoint);
  const receiptResponse = await apiRequest(`operations/${encodeURIComponent(checkpointOperationId)}`,
    { headers: { "X-Tempo-Work-Class": "background" } });
  assert.equal(receiptResponse.status, 200, "Background checkpoint receipt remains readable after worker completion");
  const checkpointReceipt = await receiptResponse.json();
  assert.equal(checkpointReceipt.state, "complete");
  assert.deepEqual(checkpointReceipt.response, persistedCheckpoint);
  run("docker", [...compose, "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo", "-v", "ON_ERROR_STOP=1", "-Atqc",
    `DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM operation_receipts WHERE operation_id='${checkpointOperationId}' AND background AND state='complete') THEN RAISE EXCEPTION 'Checkpoint receipt is not background'; END IF; IF (SELECT count(*) FROM opening_evidence_events WHERE attempt_id='${backgroundCheckpoint.attempt_id}') != 1 THEN RAISE EXCEPTION 'Checkpoint replay duplicated events'; END IF; END $$;`]);
  console.log("PASS test_postgres_background_opening_checkpoint_preserves_foreground_progress_and_replay");
  await waitForStudyableImport(importedStudy.repertoire_id);
  await waitForStudyableImport(importedBackground.repertoire_id);
  const savedTeaching = await get(`cards/${reviewCard.id}/teaching`);
  assert(savedTeaching.states.some((state) => state.revision === reviewCard.revision && state.ply === 0));
  const savedAnnotations = await get(`repertoires/${importedStudy.repertoire_id}/annotations?fen=${encodeURIComponent(reviewCard.start_fen)}`);
  assert(savedAnnotations.annotations.some((item) => item.comment === annotation.comment
    && item.arrows.length === 1 && item.squares.length === 1));
  const reinforcementQueue = await get("queue/today");
  const reinforcement = reinforcementQueue.cards.find((card) => card.id === reviewCard.id);
  assert.equal(reinforcement?.attempt_state, "reinforcement");

  const beforeBuryQueue = await get("queue/today");
  const buriedCard = beforeBuryQueue.cards[0];
  assert(buriedCard, "Durability fixture includes an active card to bury");
  const beforeBuryState = stableStudyState(await readStudySnapshot(), buriedCard.repertoire_id);
  const buryOperationId = `pg-study-bury-${randomBytes(12).toString("hex")}`;
  const burial = await postCommand(`queue/entries/${buriedCard.queue_entry_id}/bury`, {}, {
    operationId: buryOperationId, label: "foreground POST bury until tomorrow",
  });
  assert.deepEqual(burial, { buried: true, queue_entry_id: buriedCard.queue_entry_id });
  const afterBuryQueue = await get("queue/today");
  assert.deepEqual(afterBuryQueue.cards.map(card => card.queue_entry_id),
    beforeBuryQueue.cards.filter(card => card.id !== buriedCard.id).map(card => card.queue_entry_id),
    "Bury excludes all active occurrences and preserves other cards' order");

  const studySnapshot = await readStudySnapshot();
  assert.equal(studySnapshot.source, "tempo-postgres");
  assert(studySnapshot.counts.reviews > 0 && studySnapshot.counts.teaching_states > 0
    && studySnapshot.counts.position_annotations > 0 && studySnapshot.counts.prefix_splits > 0);
  const beforeRestartState = stableStudyState(studySnapshot, importedStudy.repertoire_id);
  const afterBuryState = stableStudyState(studySnapshot, buriedCard.repertoire_id);
  assert.deepEqual(afterBuryState.cards, beforeBuryState.cards, "Bury leaves scheduling unchanged");
  assert.deepEqual(afterBuryState.reviews, beforeBuryState.reviews, "Bury records no review");
  const savedBuriedEntry = studySnapshot.tables.daily_queue.find(row => row.id === buriedCard.queue_entry_id);
  assert.equal(savedBuriedEntry?.status, "buried");
  assert(beforeRestartState.reviews.some((row) => row.card_id === reviewCard.id));
  assert(beforeRestartState.queue.some((row) => row.card_id === guidedCard.id
    && (row.attempt_failed === 1 || row.attempt_state === "failed" || row.attempt_state === "guided")));
  assert(beforeRestartState.prefixSplits.some((row) => row.source_card_id === splitCard.id));

  const savedReviewCount = beforeRestartState.reviews.length;
  run("docker", [...compose, "down"]);
  run("docker", [...compose, "up", "--no-build", "-d"]);
  await waitForReady();
  await waitForStudyableImport(importedStudy.repertoire_id);
  await waitForStudyableImport(importedBackground.repertoire_id);
  const afterRestartSnapshot = await readStudySnapshot();
  await verifyPgnImportReplayWithoutDuplicates(pgnImportOperationId, importedStudy, "after recreation");
  await verifyDiscardedPgnReplay(discardedPgnId);
  assert.deepEqual(stableStudyState(afterRestartSnapshot, importedStudy.repertoire_id), beforeRestartState,
    "Authoritative study identities, values, scheduling, annotation, queue, and split survive service recreation");
  const replayedReview = await postCommand(`cards/${reviewCard.id}/review`, reviewBody, {
    operationId: reviewOperationId, label: "foreground replay confirmed review after recreation",
  });
  assert(replayedReview, "Previously confirmed review command replays under its original identity");
  const afterReplaySnapshot = await readStudySnapshot();
  assert.equal(stableStudyState(afterReplaySnapshot, importedStudy.repertoire_id).reviews.length,
    savedReviewCount, "Confirmed review replay does not create a duplicate business effect");
  assert.deepEqual(afterRestartSnapshot.tables.daily_queue.find(row => row.id === buriedCard.queue_entry_id),
    savedBuriedEntry, "Buried queue entry survives service recreation");
  const afterRestartQueue = await get("queue/today");
  assert(!afterRestartQueue.cards.some(card => card.id === buriedCard.id), "Buried card stays absent after service recreation and refresh");
  const replayedBurial = await postCommand(`queue/entries/${buriedCard.queue_entry_id}/bury`, {}, {
    operationId: buryOperationId, label: "foreground replay confirmed bury after recreation",
  });
  assert.deepEqual(replayedBurial, burial, "Confirmed burial replays its original receipt");
  assert.deepEqual((await get("queue/today")).cards.map(card => card.queue_entry_id),
    afterRestartQueue.cards.map(card => card.queue_entry_id), "Burial replay cannot bury the next card");
  console.log("PASS PostgreSQL bury until tomorrow survives recreation and idempotent replay without grading");
  console.log("PASS PostgreSQL study state, queue order, guided failure, and command identity survive service recreation");
  // Release the fixtures after their restart/replay proof completes.
  const completedFixtureIds = [importedStudy.repertoire_id, importedBackground.repertoire_id];
  for (const repertoireId of completedFixtureIds) {
    await postCommand(`repertoires/${repertoireId}`, {}, { method: "DELETE" });
  }
  const afterFixtureCleanup = await readStudySnapshot();
  assert(!afterFixtureCleanup.tables.repertoires.some(row => completedFixtureIds.includes(row.id)),
    "Completed study fixtures are removed before compatibility retention");
  const remainingFixtureTasks = readScopedPostgresRows(`SELECT COALESCE(json_agg(row_to_json(task)),'[]'::json)
    FROM (SELECT kind,state FROM background_tasks WHERE state IN ('queued','leased','retrying')
      AND (deduplication_key IN ('${completedFixtureIds.join("','")}')
        OR payload_json::jsonb->>'repertoire_id' IN ('${completedFixtureIds.join("','")}'))) task`);
  assert.deepEqual(remainingFixtureTasks, [], "Completed study fixtures leave no active background tasks");
  console.log("PASS test_postgres_completed_study_fixtures_release_background_work_before_retention");
  activeStudyRepertoireId = null;
}

async function verifyStudyBurialRetainsQuota() {
  const settingsBeforeFixture = await get("settings");
  // stdin keeps this test fixture outside the product image. It runs only
  // against the unique disposable compose project created by this runner.
  const regression = spawnSync("docker", [...compose, "exec", "-T", "-e", "TEMPO_TEST_INSTANCE=disposable", "foreground-worker", "python", "-"], {
    input: readFileSync("tests/fixtures/postgres-study-burial-quota.py", "utf8"), encoding: "utf8", env: environment,
  });
  assert.equal(regression.status, 0, regression.stderr);
  const { unrelated_order: unrelatedOrder, queue_date: queueDate } = JSON.parse(regression.stdout.trim());
  const beforeRefresh = await get("queue/today");
  await postCommand("settings", { ...settingsBeforeFixture, study_new_per_day: 1 }, { method: "PUT" });
  let refreshedQueue;
  const deadline = performance.now() + 60_000;
  while (performance.now() < deadline) {
    const queue = await get("queue/today");
    assert.notEqual(queue.projection?.state, "failed", queue.projection?.last_error);
    if (queue.projection?.state === "ready" && !queue.projection.refresh_pending &&
        queue.projection.generation > beforeRefresh.projection.generation) {
      refreshedQueue = queue;
      break;
    }
    await measureScenario.wait(250);
  }
  assert(refreshedQueue, "Study burial quota refresh publishes a new generation before its deadline");
  assert.deepEqual(refreshedQueue.cards.map(card => card.queue_entry_id), unrelatedOrder,
    "Refreshing after Study burial neither admits a replacement nor reorders unrelated cards");
  const recorded = readScopedPostgresRows(`SELECT COALESCE(json_agg(row_to_json(q)),'[]'::json)
    FROM (SELECT card_id,status FROM daily_queue WHERE queue_date='${queueDate}'
      AND card_id IN ('pg-bury-quota-a','pg-bury-quota-b')) q`);
  assert.deepEqual(recorded, [{ card_id: "pg-bury-quota-a", status: "buried" }]);
  await postCommand("settings", settingsBeforeFixture, { method: "PUT" });
  console.log("PASS PostgreSQL buried Study admission retains quota through materialization and locked candidate replay");
}

async function verifyBlockedBurialRecovery() {
  // The previous quota fixture restores settings asynchronously. Wait for its
  // queue publication before selecting the original payload and order snapshot.
  let before;
  const deadline = performance.now() + 60_000;
  while (performance.now() < deadline) {
    const queue = await get("queue/today");
    assert.notEqual(queue.projection?.state, "failed", queue.projection?.last_error);
    if (queue.projection?.state === "ready" && !queue.projection.refresh_pending) {
      before = queue;
      break;
    }
    await measureScenario.wait(250);
  }
  assert(before, "Queue settings restoration settles before blocked burial recovery");
  const selected = before.cards[0];
  assert(selected, "Blocked burial fixture has an authoritative active entry");
  const originalState = stableStudyState(await readStudySnapshot(), selected.repertoire_id);
  const operationId = "pg-study-blocked-bury";
  const prepared = spawnSync("docker", [...compose, "exec", "-T", "foreground-worker", "python", "-",
    String(selected.queue_entry_id), operationId], {
    input: readFileSync("tests/fixtures/postgres-blocked-burial.py", "utf8"), encoding: "utf8", env: environment,
  });
  assert.equal(prepared.status, 0, prepared.stderr);
  const replay = await apiRequest(`queue/entries/${selected.queue_entry_id}/bury`, {
    method: "POST", headers: { "Idempotency-Key": operationId },
  });
  assert.equal(replay.status, 202);
  assert.equal((await replay.json()).state, "blocked", "Ordinary command replay cannot resume a blocked receipt");
  const result = await postCommand(`operations/${operationId}/retry`, {});
  assert.deepEqual(result, { buried: true, queue_entry_id: selected.queue_entry_id });
  const receipt = await get(`operations/${operationId}`);
  assert.equal(receipt.state, "complete");
  assert.equal(receipt.retry_cycle, 1);
  assert.equal(receipt.attempt_count, 2);
  const after = await get("queue/today");
  assert.deepEqual(after.cards.map(card => card.queue_entry_id),
    before.cards.filter(card => card.id !== selected.id).map(card => card.queue_entry_id));
  const afterState = stableStudyState(await readStudySnapshot(), selected.repertoire_id);
  assert.deepEqual(afterState.cards, originalState.cards, "Blocked recovery leaves scheduling unchanged");
  assert.deepEqual(afterState.reviews, originalState.reviews, "Blocked recovery creates no review");
  assert.deepEqual(await postCommand(`queue/entries/${selected.queue_entry_id}/bury`, {}, { operationId }), result);
  assert.deepEqual((await get("queue/today")).cards.map(card => card.queue_entry_id),
    after.cards.map(card => card.queue_entry_id), "Recovered receipt replay never buries the next card");
  console.log("PASS PostgreSQL blocked burial resumes original payload through retry endpoint without duplicate effects");
}

// Optional release-owned candidate reuse. Direct invocations retain their usual path.
const candidateMode = process.env.TEMPO_RELEASE_CANDIDATE_MODE;
const candidateReceiptPath = process.env.TEMPO_RELEASE_CANDIDATE_RECEIPT;
const candidateOwner = process.env.TEMPO_RELEASE_CANDIDATE_OWNER;
let candidateIdentity, candidateBake, candidateMetadataPath, borrowedCandidate, candidatePublished = false;
const candidateRoles = ["schema", "api", "foreground-worker", "background-worker", "background-scheduler", "web"];
const retainedCandidateTags = [];
const candidateSuiteTags = [];
function candidateCommand(command, args, env = environment) {
  const result = spawnSync(command, args, { encoding: "utf8", env, timeout: 30_000 });
  if (result.error || result.status !== 0) throw new Error(`Candidate ${command} inspection failed`);
  return result.stdout.trim();
}
function candidateInspect(id) {
  const values = JSON.parse(candidateCommand("docker", ["image", "inspect", id]));
  if (!Array.isArray(values) || values.length !== 1) throw new Error("Candidate image inspection incomplete");
  return values[0];
}
function candidateSource() {
  const revision = candidateCommand("git", ["rev-parse", "HEAD"]), tree = candidateCommand("git", ["rev-parse", "HEAD^{tree}"]);
  const ownedSecrets = relative(process.cwd(), secretsDirectory);
  if (!/^\.tempo-pg-test-secrets-[^/]+$/.test(ownedSecrets)) throw new Error("Candidate secret directory is outside its owned source boundary");
  const trackedChanges = candidateCommand("git", ["--no-optional-locks", "status", "--porcelain", "--untracked-files=no"]);
  const changes = candidateCommand("git", ["--no-optional-locks", "status", "--porcelain", "--untracked-files=all", "--", ".", `:(top,exclude,literal)${ownedSecrets}`]);
  if (revision !== candidateRevision || trackedChanges || changes) throw new Error("Candidate release source is not exact and clean");
  return { revision, tree, clean: true };
}
function candidateTarget() {
  if (["DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"].some(name => environment[name]))
    throw new Error("Candidate reuse requires a named Docker context without host or TLS overrides");
  const context = candidateCommand("docker", ["context", "show"]);
  if (!context) throw new Error("Candidate Docker context unavailable");
  environment.DOCKER_CONTEXT = context;
  const daemon_id = candidateCommand("docker", ["info", "--format", "{{.ID}}"]);
  let platform = candidateCommand("docker", ["info", "--format", "{{.OSType}}/{{.Architecture}}"]);
  platform = platform.replace("linux/aarch64", "linux/arm64").replace("linux/x86_64", "linux/amd64");
  return { context, daemon_id, platform };
}
function candidatePrepareIdentity() {
  if (!["produce", "borrow"].includes(candidateMode) || (candidateMode === "produce" && !["lifecycle", "lifecycle-current"].includes(options.mode))
      || !/^[-a-z0-9]{12,80}$/.test(candidateOwner ?? "")
      || !candidateReceiptPath || resolve(candidateReceiptPath) !== candidateReceiptPath)
    throw new Error("Candidate reuse requires produce or borrow, an absolute receipt path and unique owner");
  const source = candidateSource(), target = candidateTarget();
  candidateBake = JSON.parse(candidateCommand("docker", [...compose, "build", "--print"]));
  const recipes = normalizeCandidateBake(candidateBake, project);
  recipes.maintenance = { context: process.cwd(), dockerfile: "Dockerfile.postgres-maintenance", labels: { "org.opencontainers.image.revision": candidateRevision } };
  return { source, ...target, recipes };
}
function candidateTag(role) { return `tempo-release-${candidateOwner}-${role}`; }
function assertCandidateTagAbsent(tag) {
  const result = spawnSync("docker", ["image", "inspect", tag], { encoding: "utf8", env: environment, timeout: 15_000 });
  if (result.error || result.status !== 1 || !/no such image/i.test(result.stderr ?? ""))
    throw new Error(`Candidate artifact tag already exists or absence is uncertain: ${tag}`);
}
function candidateRuntimeDependencies() {
  return { postgres: candidateInspect("postgres:18.6-trixie").Id, redis: candidateInspect("redis:7-alpine").Id };
}

const actions = {
  compose_config: async () => {
    const config = spawnSync("docker", [...compose, "--profile", "maintenance", "config", "--format", "json"],
      { encoding: "utf8", env: environment });
    assert.equal(config.status, 0, config.stderr);
    const stack = JSON.parse(config.stdout);
    assert.equal(stack.name, project);
    assertDisposableVolumes(stack, project, ["postgres-test-data", "redis-test-data", "postgres-test-backups"], [
      ["postgres", "/var/lib/postgresql", "postgres-test-data"], ["redis", "/data", "redis-test-data"],
      ["postgres-backup", "/backups", "postgres-test-backups"],
    ]);
    const rehearsalProject = `${project}-rehearsal`;
    const rehearsal = spawnSync("docker", ["compose", "-p", rehearsalProject, "-f", "docker-compose.postgres-rehearsal.yml",
      "--profile", "backups", "config", "--format", "json"], { encoding: "utf8", env: { ...environment,
        TEMPO_POSTGRES_PGPASS_FILE: join(secretsDirectory, "admin_pgpass") } });
    assert.equal(rehearsal.status, 0, rehearsal.stderr);
    assertDisposableVolumes(JSON.parse(rehearsal.stdout), rehearsalProject,
      ["tempo-postgres-rehearsal", "tempo-redis-rehearsal", "tempo-postgres-backups"],
      [["postgres", "/var/lib/postgresql", "tempo-postgres-rehearsal"], ["redis", "/data", "tempo-redis-rehearsal"],
        ["postgres-backup", "/backups", "tempo-postgres-backups"]]);
    assert.equal(stack.services.api.environment.TEMPO_DATABASE_READ_URL,
      "postgresql://tempo_reader@postgres:5432/tempo");
    assert.equal(stack.services.api.environment.TEMPO_REDIS_URL, "redis://redis:6379/0");
    assert.equal(stack.services.api.environment.TEMPO_DATABASE_WRITE_URL, undefined);
    assert.equal(stack.services.api.environment.TEMPO_DB_PATH, undefined);
    assert(!JSON.stringify(stack.services.api.volumes ?? []).includes("tempo-data"));
    assert.equal(stack.services["background-worker"].environment.TEMPO_DATABASE_WRITE_URL,
      "postgresql://tempo_writer@postgres:5432/tempo");
    assert.equal(stack.services.web.ports[0].host_ip, "127.0.0.1");
    assert.equal(Number(stack.services.web.ports[0].published), testPort);
    assert(Object.values(stack.networks ?? {}).every((network) => !network.external
      && network.name.startsWith(`${project}_`)));
    console.log("PASS PostgreSQL API has reader credentials and no SQLite mount");
    const defaultConfig = spawnSync("docker", ["compose", "-f", "docker-compose.yml",
      "config", "--format", "json"], { encoding: "utf8", env: environment });
    assert.equal(defaultConfig.status, 0, defaultConfig.stderr);
    const defaultStack = JSON.parse(defaultConfig.stdout);
    for (const resolvedStack of [stack, defaultStack])
      assert.equal(resolvedStack.services["maia-worker"], undefined, "retired Maia service must not be packaged");
    assert(defaultStack.services.postgres && defaultStack.services["foreground-worker"]);
    assert.equal(defaultStack.services.api.environment.TEMPO_DATABASE_WRITE_URL, undefined);
    assert.equal(defaultStack.services.api.environment.TEMPO_DB_PATH, undefined);
    assert(!JSON.stringify(defaultStack.services.api.volumes ?? []).includes("tempo-data"));
    const backupCommand = defaultStack.services["postgres-backup"].command;
    assert.equal(backupCommand.length, 1, "backup loop must be one shell argument");
    const backupSyntax = spawnSync("sh", ["-n", "-c", backupCommand[0].replaceAll("$$", "$")],
      { encoding: "utf8" });
    assert.equal(backupSyntax.status, 0, backupSyntax.stderr);
    console.log("PASS recurring PostgreSQL backup loop has valid shell syntax");
    console.log("PASS default Compose selects PostgreSQL and keeps SQLite isolated");
    verifyProjectIsUnused();
    verifyDisposableVolumesAbsent(["postgres-test-data", "redis-test-data", "postgres-test-backups"].map(name => `${project}_${name}`));
    verifyDisposableVolumesAbsent(["tempo-postgres-rehearsal", "tempo-redis-rehearsal", "tempo-postgres-backups"].map(name => `${rehearsalProject}_${name}`));
  },
  image_build: async () => {
    await waitForStudyQuiet();
    if ((stages.includes("deployment_lifecycle") || stages.includes("deployment_lifecycle_current")) && environment.TEMPO_LIFECYCLE_SCHEMA62_ROOT) {
      const context = spawnSync("docker", ["context", "show"], { encoding: "utf8", env: environment, timeout: 15_000 });
      assert.equal(context.status, 0, "Cannot select schema-62 fixture Docker context");
      environment.DOCKER_CONTEXT = context.stdout.trim();
      const { manifest: bridge } = readSchema62Bridge(environment);
      const observed = await inspectImmutableImageSet(async args => {
        const result = spawnSync("docker", ["--context", environment.DOCKER_CONTEXT, ...args], { encoding: "utf8", env: environment, timeout: 15_000 });
        if (result.error) throw result.error;
        return { code: result.status, stdout: result.stdout };
      }, Object.values(bridge.images));
      for (const [role, id] of Object.entries(bridge.images)) {
        const image = observed.get(id);
        assert(image?.Os === "linux" && image.Architecture === "arm64"
          && image.Config?.Labels?.["org.opencontainers.image.revision"] === bridge.revision,
        `Schema-62 fixture identity or platform mismatch for ${role}`);
      }
      atomicJson(`test-results/tempo-cli/${project}/schema62-preparation.json`, {
        revision: bridge.revision, tree: bridge.tree, root: environment.TEMPO_LIFECYCLE_SCHEMA62_ROOT,
        context: environment.DOCKER_CONTEXT, images: bridge.images, acquisition: "borrowed-local", builds: 0, pulls: 0,
      });
    }
    if ((stages.includes("deployment_lifecycle") || stages.includes("deployment_lifecycle_current")) && environment.TEMPO_LIFECYCLE_P0_59_ONLY !== "1"
      && !environment.TEMPO_LIFECYCLE_SCHEMA61_IMAGES) {
      const context = spawnSync("docker", ["context", "show"], { encoding: "utf8", env: environment, timeout: 15_000 });
      assert.equal(context.status, 0, "Cannot select historical image Docker context");
      environment.DOCKER_CONTEXT = context.stdout.trim();
      const lockPath = environment.TEMPO_LIFECYCLE_IMAGE_LOCK ?? "config/lifecycle-images/schema61-linux-arm64.json";
      const outputPath = join(secretsDirectory, "schema61-images.json");
      const historicalEvidence = await prepareLifecycleImages({
        lock: JSON.parse(readFileSync(lockPath, "utf8")), context: environment.DOCKER_CONTEXT, outputPath,
      });
      // Only a successful complete acquisition may become the lifecycle input.
      environment.TEMPO_LIFECYCLE_SCHEMA61_IMAGES = outputPath;
      atomicJson(`test-results/tempo-cli/${project}/historical-preparation.json`, historicalEvidence);
    }
    if (candidateMode) {
      candidateIdentity = candidatePrepareIdentity();
      for (const role of candidateRoles) assertCandidateTagAbsent(`${project}-${role}`);
      if (candidateMode === "produce") {
        if (existsSync(candidateReceiptPath)) throw new Error("Candidate receipt already exists");
        assertCandidateTagAbsent(maintenanceImage);
        for (const role of [...candidateRoles, "maintenance"]) assertCandidateTagAbsent(candidateTag(role));
        candidateMetadataPath = join(secretsDirectory, "candidate-bake-metadata.json");
      } else {
        const receipt = JSON.parse(readFileSync(candidateReceiptPath, "utf8"));
        candidateIdentity.build_dependencies = receipt.identity?.build_dependencies;
        candidateIdentity.runtime_dependencies = candidateRuntimeDependencies();
        const inspected = await inspectImmutableImageSet(async args => ({ code: 0, stdout: candidateCommand("docker", args) }),
          Object.values(receipt.images ?? {}));
        borrowedCandidate = await verifyCandidateReceipt({ receipt, owner: candidateOwner, observed: candidateIdentity,
          inspect: async id => inspected.get(id) });
      }
    }
    await recordBudgetResource(`compose:${project}`, true);
    resourcesCreated = true;
    if (borrowedCandidate) {
      assertCandidateTagAbsent(maintenanceImage);
      for (const role of candidateRoles) {
        const tag = `${project}-${role}`; candidateSuiteTags.push(tag);
        run("docker", ["tag", borrowedCandidate[role], tag]);
      }
      maintenanceImageCleanupRequired = true;
      run("docker", ["tag", borrowedCandidate.maintenance, maintenanceImage]);
    } else {
      if (candidateMode === "produce") candidateSuiteTags.push(...candidateRoles.map(role => `${project}-${role}`));
      await managedComposeBuild(() => execFileSync('docker',[...compose,'build','--print'],{encoding:'utf8',env:environment}),
      args => run('docker', candidateMetadataPath ? [...args, "--metadata-file", candidateMetadataPath] : args,
        candidateMetadataPath ? { env: { ...environment, BUILDX_METADATA_PROVENANCE: "min" } } : {}));
    }
    // The rehearsal records dependency images before starting its child stack.
    // Standalone mode has no parent startup to pull these images first.
    if (["lifecycle", "lifecycle-current"].includes(options.mode)) run("docker", [...compose, "pull", "--policy", "missing", "postgres", "redis"]);
  },
  maintenance_cli: async () => {
    maintenanceImageCleanupRequired = true;
    const maintenanceMetadata = candidateMode === "produce" ? join(secretsDirectory, "candidate-maintenance-metadata.json") : null;
    if (!borrowedCandidate) await managedBuild(builder => run("docker", ['buildx', "build", '--builder', builder, '--load', "-f", "Dockerfile.postgres-maintenance", "--label", `org.opencontainers.image.revision=${candidateRevision}`, "-t", maintenanceImage, ...(maintenanceMetadata ? ["--metadata-file", maintenanceMetadata] : []), "."],
      maintenanceMetadata ? { env: { ...environment, BUILDX_METADATA_PROVENANCE: "min" } } : {}));
    for (const script of ["apply_postgres_migrations.py", "verify_postgres_backup.py", "verify_postgres_cli_state.py"]) {
      run("docker", ["run", "--rm", "--name", maintenanceHelpContainer, "--memory", "256m", "--label", "tempo.resource-class=development", "--label", `tempo.resource-owner=${process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN}`, "--label", `com.docker.compose.project=${project}`, "--label", "com.docker.compose.service=maintenance-help", maintenanceImage, `database/tooling/${script}`, "--help"]);
    }
    if (candidateMode === "produce") {
      const metadata = JSON.parse(readFileSync(candidateMetadataPath, "utf8"));
      const maintenance = JSON.parse(readFileSync(maintenanceMetadata, "utf8"));
      const imageIds = {}, materials = {};
      for (const role of [...candidateRoles, "maintenance"]) {
        const tag = role === "maintenance" ? maintenanceImage : `${project}-${role}`;
        const inspected = candidateInspect(tag);
        if (inspected.Config?.Labels?.["org.opencontainers.image.revision"] !== candidateRevision)
          throw new Error(`Candidate ${role} revision mismatch`);
        imageIds[role] = inspected.Id;
        const path = role === "maintenance" ? "Dockerfile.postgres-maintenance" : candidateIdentity.recipes[role].dockerfile;
        const roleMaterials = buildMaterials(role === "maintenance" ? maintenance : metadata[role], {
          dockerfile: readFileSync(path, "utf8"), platform: candidateIdentity.platform, expectedImageId: inspected.Id });
        for (const [name, digest] of Object.entries(roleMaterials)) materials[`${role}:${name}`] = digest;
      }
      candidateIdentity.build_dependencies = materials;
      candidateIdentity.runtime_dependencies = candidateRuntimeDependencies();
      candidateSource();
      for (const role of [...candidateRoles, "maintenance"]) {
        const tag = candidateTag(role); assertCandidateTagAbsent(tag);
        retainedCandidateTags.push(tag); run("docker", ["tag", imageIds[role], tag]);
      }
      writeCandidateReceipt(candidateReceiptPath, { owner: candidateOwner, identity: candidateIdentity, images: imageIds });
      candidatePublished = true;
    }
    console.log("PASS PostgreSQL maintenance image starts migration and verification commands");
  },
  startup: async () => {
    await waitForStudyQuiet();
    run("docker", [...compose, "up", "--no-build", "-d"]);
    await waitForReady();
  },
  service_health: async () => {
    const runningContainers = spawnSync("docker", [...compose, "ps", "--format", "json"],
      { encoding: "utf8", env: environment });
    assert.equal(runningContainers.status, 0, runningContainers.stderr);
    const runningServices = new Set(runningContainers.stdout.trim().split("\n")
      .filter(Boolean).map(line => JSON.parse(line))
      .filter(container => container.State === "running")
      .map(container => container.Service));
    for (const service of ["api", "foreground-worker", "background-worker",
      "background-scheduler", "web"])
      assert(runningServices.has(service), `${service} exited during PostgreSQL startup`);
  },
  game_review_reader: async () => {
    run(resolvePython(), ["database/tests/check_postgres_game_review_reader.py"], {
      env: { ...environment,
        PYTHONPATH: ["backend", "database", "backend/tests"].map(path => resolve(path)).join(":"),
        TEMPO_TEST_INSTANCE: "disposable",
        TEMPO_TEST_COMPOSE_PROJECT: project,
        TEMPO_DEVELOPMENT_BUDGET_TOKEN: process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN,
      },
    });
  },
  background_budget: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/database/tests/check_postgres_background_budget.py"]);
  },
  operation_recovery: async () => {
    await runAllocatedNativeProof('/source/database/tests/check_postgres_operation_recovery.py',
      'test_postgres_operation_recovery_uses_bounded_maintenance_allocation');
  },
  schema_migrations: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/database/tests/check_postgres_study_only.py"]);
    await verifyStudyPriorityMaintenance();
    await verifyStudyOpeningAdmission();
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/database/tests/check_postgres_upgrade.py"]);
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/database/tests/check_postgres_historical_source_upgrade.py"]);
  },
  background_diagnostics: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/database/tests/check_postgres_background_diagnostics.py"]);
  },
  deployment_lifecycle_current: async () => {
    await verifyTempoCliLifecycle({ project, environment, revision: candidateRevision, profile: "current63",
      composeFiles: [join(process.cwd(), "docker-compose.postgres.test.yml"), resourceLimits, buildLabels], measure: measureScenario });
  },
  deployment_lifecycle: async () => {
    const verifyLifecycle = () => verifyTempoCliLifecycle({ project, environment, revision: candidateRevision,
      composeFiles: [join(process.cwd(), "docker-compose.postgres.test.yml"), resourceLimits, buildLabels], measure: measureScenario });
    if (options.mode === "lifecycle") {
      if (environment.TEMPO_LIFECYCLE_MANAGED_REHEARSAL === '1') {
        await verifyTempoCliLifecycle({ project, environment: { ...environment, W08_COPY_ONLY:'1' }, revision: candidateRevision,
          composeFiles: [join(process.cwd(), "docker-compose.postgres.test.yml"), resourceLimits, buildLabels], measure: measureScenario });
        await verifyTempoCliLifecycle({ project, environment: { ...environment, W08_CAPTURE_ONLY:'1' }, revision: candidateRevision,
          composeFiles: [join(process.cwd(), "docker-compose.postgres.test.yml"), resourceLimits, buildLabels], measure: measureScenario });
      }
      await verifyLifecycle();
      return;
    }
    const consumers = ["postgres", "redis", "api", "foreground-worker", "background-worker", "background-scheduler", "web"];
    run("docker", [...compose, "stop", ...consumers]);
    try {
      await verifyLifecycle();
    } finally {
      run("docker", [...compose, "up", "--no-build", "-d"]);
      await waitForReady();
    }
  },
  stale_delivery: async () => {
    // Broker witness runs against the owned foreground/background workers, then
    // repeats identical logical deliveries after their scheduler and workers restart.
    const staleWitness = phase => {
      const result = run("docker", [...compose, "run", "--rm", "--no-deps",
        "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
        "/source/database/tests/check_postgres_background_workloads.py", "--stale-delivery", phase, project],
      { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
      const marker = result.stdout.split("\n").find(line => line.startsWith("STALE_DELIVERY_WITNESS "));
      assert(marker, `Stale delivery ${phase} witness did not emit evidence`);
      return JSON.parse(marker.slice("STALE_DELIVERY_WITNESS ".length));
    };
    const staleBefore = staleWitness("before");
    run("docker", [...compose, "restart", "foreground-worker", "background-worker", "background-scheduler"]);
    await waitForReady({ requireContainerHealthy: true });
    const staleAfter = staleWitness("after");
    assert.deepEqual(staleAfter.snapshot, staleBefore.snapshot, "original retired identities and state survive restart and replay");
    assert.deepEqual(staleAfter.queue, staleBefore.queue, "retained queue completion survives worker restart");
    atomicJson(`test-results/native/${project}-stale-delivery.json`, { revision: candidateRevision, project,
      workers_restarted: ["foreground-worker", "background-worker", "background-scheduler"],
      before: staleBefore, after: staleAfter });
    console.log("PASS stale retired broker deliveries terminate without regrowth and Study queue completes across worker restart");
  },
  background_workloads: async () => {
    await runAllocatedNativeProof("/source/database/tests/check_postgres_daily_study_dispatch.py",
      "test_postgres_daily_dispatch_uses_bounded_maintenance_allocation", ["-e", "TEMPO_REDIS_URL=redis://redis:6379/0"]);
    await actions.stale_delivery();
    await executeIsolatedBackgroundWorkload({
      stopConsumers: () => {
        run("docker", [...compose, "stop", ...workloadConsumers]);
        verifyWorkloadConsumers("exited");
      },
      measureWorkload: () => {
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/database/tests/check_postgres_queue_attempt_recovery.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/database/tests/check_postgres_training_transition.py", "proof", `training-transition-${randomBytes(16).toString("hex")}`]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", `TEMPO_TEST_COMPOSE_PROJECT=${project}`,
          "schema", "python", "/source/database/tests/check_postgres_redis_fixture_isolation.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
          "/source/database/tests/check_postgres_repertoire_limits.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
          "/source/database/tests/check_postgres_opening_capacity.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
          "/source/database/tests/check_postgres_deletion.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
          "/source/database/tests/check_postgres_tactic_capture.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/database/tests/check_postgres_background_workloads.py"]);
        run("docker", [...compose, "exec", "-T", "api", "python", "-c",
          "import os; assert os.environ.get('TEMPO_DATABASE_READ_URL'); assert 'TEMPO_DATABASE_WRITE_URL' not in os.environ; print('PASS deployed API has reader URL and no writer credentials')"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/database/tests/check_postgres_opening_evidence.py"]);
      },
      restoreConsumers: () => restoreBackgroundWorkloadConsumers({
        startConsumers: (services) => run("docker", [...compose, "start", ...services]),
        waitForReady: () => waitForReady({ requireContainerHealthy: true }),
        verifyConsumers: verifyWorkloadConsumers,
      }),
    });
  },
  command_recreation: async () => {
    const settings = await get("settings");
    const importedLimit = await importFixture("repertoire-limit-recreation.pgn", repertoireLimitRecreationPgn);
    limitRecreationRepertoireId = importedLimit.repertoire_id;
    await waitForStudyableImport(importedLimit.repertoire_id);
    const before = await get("queue/today");
    const operationId = `pg-durability-${randomBytes(12).toString("hex")}`;
    const updatedSettings = { ...settings, new_cards_per_day: settings.new_cards_per_day + 1 };
    const sendSettings = () => apiRequest("settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "Idempotency-Key": operationId },
      body: JSON.stringify(updatedSettings),
    });
    const limitRepertoire = (await get("repertoires")).repertoires.find(item => item.id === importedLimit.repertoire_id);
    assert(limitRepertoire, "Recreation fixture must exist before testing its override");
    const overridePayload = { new_cards_per_day: updatedSettings.new_cards_per_day };
    const overrideOperationId = `pg-limit-${randomBytes(12).toString("hex")}`;
    await postCommand(`repertoires/${limitRepertoire.id}/settings`, overridePayload,
      { method: "PUT", operationId: overrideOperationId });
    const uncertainResponse = await sendSettings();
    assert(uncertainResponse.ok, `Settings command accepted before simulating a lost response: ${uncertainResponse.status}`);
    await uncertainResponse.body?.cancel();
    let settingsCommitted = false;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      measureScenario.poll();
      if ((await get("settings")).new_cards_per_day === updatedSettings.new_cards_per_day) {
        settingsCommitted = true;
        break;
      }
      await measureScenario.wait(100);
    }
    assert(settingsCommitted, "Settings business effect commits after its response is discarded");
    run("docker", [...compose, "down"]);
    run("docker", [...compose, "up", "--no-build", "-d"]);
    await waitForReady();
    assert.equal((await get("settings")).new_cards_per_day, updatedSettings.new_cards_per_day);
    await confirm(await sendSettings());
    const restored = (await get("repertoires")).repertoires.find(item => item.id === limitRepertoire.id);
    assert.equal(restored.new_cards_per_day, overridePayload.new_cards_per_day);
    assert.equal(restored.effective_new_cards_per_day, overridePayload.new_cards_per_day);
    await postCommand(`repertoires/${limitRepertoire.id}/settings`, overridePayload,
      { method: "PUT", operationId: overrideOperationId });
    assert.equal((await get(`operations/${overrideOperationId}`)).state, "complete");
    const receipt = await get(`operations/${operationId}`);
    assert.equal(receipt.state, "complete");
    const after = await get("queue/today");
    assert.deepEqual(after.cards.map(card => card.queue_entry_id),
      before.cards.map(card => card.queue_entry_id));
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/database/tests/check_postgres_opening_evidence.py", "--verify-persisted"]);
    console.log("PASS PostgreSQL lost-response receipt replay, settings, and queue order survive container recreation");
  },
  backup_restore: async () => {
    run("docker", [...compose, "stop", "api", "foreground-worker", "background-worker",
      "background-scheduler", "web"]);
    run("docker", [...compose, "exec", "-T", "postgres", "sh", "-ec",
      "pg_dump -U postgres -d tempo -Fc -f /tmp/tempo-test.dump && " +
      "pg_restore -l /tmp/tempo-test.dump >/dev/null && " +
      "createdb -U postgres tempo_restore_check && " +
      "pg_restore -U postgres -d tempo_restore_check /tmp/tempo-test.dump"]);
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/database/tooling/verify_postgres_backup.py",
      "postgresql://postgres@postgres:5432/tempo",
      "postgresql://postgres@postgres:5432/tempo_restore_check"]);
    run("docker", [...compose, "exec", "-T", "postgres", "sh", "-ec",
      "dropdb -U postgres tempo_restore_check && rm /tmp/tempo-test.dump"]);
    console.log("PASS every PostgreSQL table matches after backup restoration");
    run("docker", [...compose, "up", "--no-build", "-d"]);
    await waitForReady();
    if (limitRecreationRepertoireId !== null) {
      await postCommand(`repertoires/${limitRecreationRepertoireId}`, {}, {
        method: "DELETE", label: "foreground DELETE recreation fixture after verified backup",
      });
      limitRecreationRepertoireId = null;
    }
  },
  browser: async () => {
      const browserArguments = buildPostgresPlaywrightArguments(options);
      const report = options.maintainedStudyProof
        ? resolve(process.env.TEMPO_CI_REPORT ?? `test-results/ci/${project}-maintained-study.json`) : process.env.TEMPO_CI_REPORT;
      run("npx", browserArguments, { env: { ...environment,
        ...(report ? {TEMPO_CI_REPORT: report} : {}),
        TEMPO_DOCKER_URL: origin,
        TEMPO_TEST_COMPOSE_PROJECT: project,
        TEMPO_TEST_OUTPUT_DIR: join(process.cwd(), "test-results", `browser-postgres-${process.pid}`),
      } });
      if (options.maintainedStudyProof) {
        verifyMaintainedBrowserReceipt(JSON.parse(readFileSync(report, 'utf8')));
        atomicJson(`test-results/native/${project}-maintained-study.json`, {revision: candidateRevision, project,
          scope: 'partial-maintained-study-not-full-E02', browser_report: report, browser_cases: 1,
          unresolved_owner_cases: unresolvedQueueCases, historical_priority_rollback: 'not executed'});
      }
  },
  maintained_study_only: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/database/tests/check_postgres_study_only.py"]);
    await verifyStudyPriorityMaintenance(true);
    await verifyStudyOpeningAdmission();
  },
  maintained_study_queue_receipts: () => verifyStudyQueueReceipts(true),
  study_isolation: async () => {
    run("docker", [...compose, "down", "-v"]);
    run("docker", [...compose, "up", "--no-build", "-d"]);
    await waitForReady();
    assert.equal((await get("queue/today")).cards.length, 0,
      "Fresh durability database starts without browser queue entries");
  },
  study_only: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/database/tests/check_postgres_study_only.py"]);
    await verifyStudyPriorityMaintenance();
    await verifyStudyOpeningAdmission();
  },
  study_queue_receipts: () => verifyStudyQueueReceipts(),
  ...Object.fromEntries(['position-grade-effects', 'position-introductions', 'position-queue-admission', 'prefix-split-placement'].map(check => [check, async () => {
    assertDevelopmentProjectOwned(project);
    const imageReference = run('docker', [...compose, 'images', '-q', 'schema'],
      {stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8'}).stdout.trim();
    assert.match(imageReference, /^(sha256:)?[a-f0-9]{12,64}$/);
    const image = JSON.parse(run('docker', ['image', 'inspect', imageReference],
      {stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8'}).stdout)[0];
    const imageId = image.Id;
    assert.match(imageId, /^sha256:[a-f0-9]{64}$/);
    assert.equal(image.Config.Labels['org.opencontainers.image.revision'], candidateRevision);
    const result = run('docker', [...compose, 'run', '--rm', '--no-deps',
      '-e', `TEMPO_TEST_COMPOSE_PROJECT=${project}`,
      '-e', `TEMPO_NATIVE_CANDIDATE_REVISION=${candidateRevision}`,
      '-e', `TEMPO_NATIVE_RUNTIME_IMAGE=${imageId}`,
      '-e', `TEMPO_NATIVE_POSITION_CHECK=${check}`,
      'schema', 'python', check === 'prefix-split-placement'
        ? '/source/database/tests/native-split-pytest.py' : '/source/database/tests/position-native-pytest.py'],
    {stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8'});
    const evidence = JSON.parse(result.stdout);
    assert.equal(evidence.head, candidateRevision);
    assert.equal(evidence.project, project);
    assert.equal(evidence.cleanup, 'dropped');
    assert.equal(evidence.tests?.length, 1);
    assert.equal(evidence.tests[0].status, 'passed');
    atomicJson(`test-results/native/${project}-${check}.json`, {
      head: candidateRevision, checks: {[check]: evidence}});
  }])),
  'redis-fixture-isolation': async () => {
    let evidence;
    await executeIsolatedBackgroundWorkload({
      stopConsumers: () => {
        run('docker', [...compose, 'stop', ...workloadConsumers]);
        verifyWorkloadConsumers('exited');
      },
      measureWorkload: () => {
        const result = run('docker', [...compose, 'run', '--rm', '--no-deps', '-e', `TEMPO_TEST_COMPOSE_PROJECT=${project}`,
          'schema', 'python', '/source/scripts/ci-native-unittest.py', 'redis-fixture-isolation'],
        {stdio: ['ignore','pipe','inherit'], encoding: 'utf8'});
        evidence = JSON.parse(result.stdout);
        assert(evidence.expected?.length && evidence.tests?.length, 'Native fixture collection did not execute');
      },
      restoreConsumers: () => restoreBackgroundWorkloadConsumers({
        startConsumers: services => run('docker', [...compose, 'up', '--no-build', '-d', '--no-deps', ...services]),
        waitForReady: () => waitForReady({ requireContainerHealthy: true }), verifyConsumers: verifyWorkloadConsumers,
      }),
    });
    atomicJson(`test-results/native/${project}.json`, {head:candidateRevision,
      planHash:process.env.TEMPO_CI_PLAN_HASH ?? null, repository:process.env.GITHUB_REPOSITORY ?? null,
      runId:Number(process.env.GITHUB_RUN_ID), attempt:Number(process.env.GITHUB_RUN_ATTEMPT),
      checks:{'redis-fixture-isolation':evidence}});
  },
  study_durability: async () => {
    await measureScenario.detail("foreground_study", "scenario", verifyForegroundAndStudyDurability);
    await measureScenario.detail("study_burial_quota", "scenario", verifyStudyBurialRetainsQuota);
    await measureScenario.detail("blocked_burial_recovery", "scenario", verifyBlockedBurialRecovery);
    // Portable stalemate mining/import is retired. Current foreground history,
    // burial quota and blocked-recovery assertions remain in the three owners above.
  },
  cleanup: async () => executeDiagnosticCleanup(() => {
    if (resourcesCreated) {
      const inspect = (args) => {
        const result = spawnSync("docker", args, { encoding: "utf8", env: environment });
        if (args[0] === "image" && args[1] === "inspect" && args[2] === maintenanceImage
          && isMissingMaintenanceImage(result)) return "[]";
        assert.equal(result.status, 0, "Could not record disposable Docker ownership");
        return result.stdout.trim();
      };
      const ids = inspect(["ps", "-aq", "--filter", `label=com.docker.compose.project=${project}`]).split(/\s+/).filter(Boolean);
      const containers = ids.length ? JSON.parse(inspect(["inspect", ...ids])).map(container => ({
        id: container.Id, name: container.Name, image: container.Image, created: container.Created,
        started_at: container.State.StartedAt, project: container.Config.Labels["com.docker.compose.project"],
        volumes: container.Mounts.filter(mount => mount.Type === "volume").map(mount => ({ name: mount.Name, destination: mount.Destination })),
      })) : [];
      atomicJson(`test-results/tempo-cli/${project}/ownership.json`, { checkout: process.cwd(), revision: candidateRevision,
        project, context: inspect(["context", "show"]), containers,
        maintenance_image: maintenanceImageCleanupRequired ? JSON.parse(inspect(["image", "inspect", maintenanceImage])).map(image => ({ id: image.Id, created: image.Created, tag: maintenanceImage })) : [],
        teardown: ["docker", ...compose, "down", "--rmi", "local", "-v"],
        maintenance_teardown: ["docker", "image", "rm", maintenanceImage] });
    }
    if (process.env.TEMPO_CI_REPORT && resourcesCreated) {
      const diagnostics = spawnSync("docker", [...compose, "logs", "--no-color", "--tail=200"], { encoding: "utf8", env: environment, maxBuffer: 10 * 1024 * 1024 });
      mkdirSync("test-results/ci", { recursive: true });
      writeFileSync(`test-results/ci/${options.mode}-${project}-services.log`, redact(`${diagnostics.stdout ?? ""}\n${diagnostics.stderr ?? ""}`,
        [administratorPassword, readerPassword, writerPassword]));
    }
  }, async () => {
    const cleanupErrors = [];
    try {
      if(resourcesCreated||maintenanceImageCleanupRequired)removeOwnedMaintenanceHelpContainer();
      if (resourcesCreated) {
        assertDevelopmentProjectOwned(project);
        const stopped = spawnSync("docker", [...compose, "down", "--rmi", "local", "-v"],
          { stdio: "inherit", env: environment });
        if (stopped.error || stopped.status !== 0) cleanupErrors.push(new Error("Disposable PostgreSQL stack cleanup failed"));
        for (const resource of ["container", "volume"]) {
          const remaining = spawnSync("docker", [resource === "container" ? "ps" : "volume", ...(resource === "container" ? ["-aq"] : ["ls", "-q"]),
            "--filter", `label=com.docker.compose.project=${project}`], { encoding: "utf8", env: environment });
          if (remaining.status !== 0 || remaining.stdout.trim()) cleanupErrors.push(new Error(`Disposable PostgreSQL ${resource} cleanup left resources`));
        }
      }
      if (maintenanceImageCleanupRequired) {
        const removedMaintenanceImage = spawnSync("docker", ["image", "rm", maintenanceImage],
          { stdio: ["ignore", "ignore", "pipe"], encoding: "utf8", env: environment });
        const ownedImageAbsent = isMissingMaintenanceImage(removedMaintenanceImage);
        if (removedMaintenanceImage.error || (removedMaintenanceImage.status !== 0 && !ownedImageAbsent))
          cleanupErrors.push(new Error("Disposable maintenance image cleanup failed"));
      }
    } finally {
      for (const tag of candidateSuiteTags) {
        const removed = spawnSync("docker", ["image", "rm", tag], { encoding: "utf8", env: environment, timeout: 15_000 });
        if (removed.error || (removed.status !== 0 && !/no such image/i.test(removed.stderr ?? "")))
          cleanupErrors.push(new Error(`Candidate suite tag cleanup failed: ${tag}`));
      }
      if (!candidatePublished) for (const tag of retainedCandidateTags) {
        const removed = spawnSync("docker", ["image", "rm", tag], { encoding: "utf8", env: environment, timeout: 15_000 });
        if (removed.error || (removed.status !== 0 && !/no such image/i.test(removed.stderr ?? "")))
          cleanupErrors.push(new Error(`Candidate tag cleanup failed: ${tag}`));
      }
      rmSync(secretsDirectory, { recursive: true, force: true });
    }
    if (cleanupErrors.length) throw new AggregateError(cleanupErrors, "PostgreSQL resource cleanup failed");
    reconcileDevelopmentStartup(project);
    await recordBudgetResource(`compose:${project}`, false);
  }),
};

let failed = false;
try {
  await executePostgresTestPlan(stages, actions, measureScenario, async () => {
    // Capture failure diagnostics before the executor tears down this test stack.
    try {
      if (activeStudyRepertoireId) await reportStudyFixtureDiagnostics(activeStudyRepertoireId);
    } finally {
      if (resourcesCreated) spawnSync("docker", [...compose, "logs", "--tail=80"],
        { stdio: "inherit", env: environment });
    }
  });
} catch (error) {
  failed = true;
  console.error(error);
}
process.exit(failed ? 1 : 0);
