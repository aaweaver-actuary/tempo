// Disposable PostgreSQL product and container-recreation gate.

import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createIsolatedTestEnvironment } from "./test-environment.mjs";
import { buildPostgresPlaywrightArguments, parsePostgresTestOptions } from "./postgres-test-options.mjs";
import { backgroundWorkloadConsumers, executeDiagnosticCleanup, executeIsolatedBackgroundWorkload, executePostgresTestPlan, postgresTestStages, restoreBackgroundWorkloadConsumers } from "./postgres-test-plan.mjs";
import { assertNoCompletedFixtureConflict, backgroundPublicationPgn,
  repertoireLimitRecreationPgn, studyDurabilityPgn } from "./postgres-test-fixture.mjs";
import { createScenarioTimer } from "./test-scenario-timings.mjs";
import { verifyTempoCliLifecycle } from "./check-tempo-cli.mjs";
import { atomicJson, redact } from "./tempo-deployment.mjs";

const options = parsePostgresTestOptions(process.argv.slice(2));
const stages = postgresTestStages(options);
if (options.list) {
  console.log(JSON.stringify({ mode: options.mode, stages,
    browser_file: options.browserFile, browser_grep: options.browserGrep }, null, 2));
  process.exit(0);
}

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
const compose = ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml"];
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
let maintenanceImageCreated = false;
const timingPath = join(process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance",
  `postgres-scenarios-${options.mode}-${project}.json`);
const commitResult = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
const candidateRevision = commitResult.stdout.trim();
const buildLabels = join(secretsDirectory, "image-labels.json");
writeFileSync(buildLabels, JSON.stringify({ services: Object.fromEntries([
  "schema", "api", "foreground-worker", "background-worker", "background-scheduler", "web", "defense-engine", "maia-worker",
].map(name => [name, { build: { labels: { "org.opencontainers.image.revision": candidateRevision } } }])) }));
compose.push("-f", buildLabels);
const measureScenario = createScenarioTimer(timingPath, {
  runner: "postgres", mode: options.mode,
  commit: commitResult.status === 0 ? commitResult.stdout.trim() : null,
  plan_hash: process.env.TEMPO_CI_PLAN_HASH ?? null,
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

function run(command, argumentsList, options = {}) {
  const result = spawnSync(command, argumentsList, { stdio: "inherit", env: environment, ...options });
  if (result.error || result.status !== 0)
    throw new Error(`${command} ${argumentsList.join(" ")} failed`);
}

const workloadConsumers = backgroundWorkloadConsumers;

function runPrefixApplicationProof(...argumentsList) {
  run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0",
    "schema", "python", "/source/scripts/check_postgres_prefix_transition_application.py", ...argumentsList]);
}

function verifyWorkloadConsumers(expectedState) {
  const result = spawnSync("docker", [...compose, "ps", "--all", "--format", "json", ...workloadConsumers],
    { encoding: "utf8", env: environment });
  assert.equal(result.status, 0, result.stderr);
  const states = new Map(result.stdout.trim().split("\n").filter(Boolean)
    .map(line => JSON.parse(line)).map(container => [container.Service, container.State]));
  for (const service of workloadConsumers)
    assert.equal(states.get(service), expectedState, `${service} must be ${expectedState} for the workload benchmark`);
}

async function waitForReady({ requireContainerHealthy = false } = {}) {
  for (let attempt = 0; attempt < 180; attempt += 1) {
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
            await new Promise(resolve => setTimeout(resolve, 1_000));
            continue;
          }
        }
        return;
      }
    } catch { /* stack startup */ }
    await new Promise(resolve => setTimeout(resolve, 1_000));
  }
  throw new Error("Disposable PostgreSQL Tempo did not become ready");
}

async function get(path) {
  const response = await apiRequest(path);
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

async function confirm(response) {
  if (response.status !== 202) {
    if (!response.ok) throw new Error(`command HTTP ${response.status} ${await response.text()}`);
    return response.json();
  }
  const pending = await response.json();
  assert(pending.operation_id, "202 command must return an operation ID");
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const receipt = await get(`operations/${encodeURIComponent(pending.operation_id)}`);
    if (receipt.state === "complete") return receipt.response;
    if (receipt.state === "failed") throw new Error(`PostgreSQL command failed: ${JSON.stringify(receipt.error)}`);
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error(`PostgreSQL operation ${pending.operation_id} remains pending`);
}

async function waitForStudyableImport(repertoireId) {
  let settledSamples = 0;
  for (let attempt = 0; attempt < 180; attempt += 1) {
    const [system, integrity, queue] = await Promise.all([
      get("system/tasks"), get(`repertoires/${repertoireId}/integrity`), get("queue/today"),
    ]);
    assertNoCompletedFixtureConflict(integrity, repertoireId);
    const graphTask = system.tasks.find((task) => task.kind === "opening_graph_rebuild"
      && task.deduplication_key === repertoireId);
    if (graphTask?.state === "failed" || integrity.scan_status === "failed"
      || queue.projection?.state === "failed") {
      throw new Error(graphTask?.last_error ?? integrity.last_scan_error ?? integrity.last_error
        ?? queue.projection?.last_error ?? "PostgreSQL import background work failed");
    }
    if (graphTask?.state === "complete" && integrity.scan_status === "idle" && integrity.status === "clean"
      && queue.projection?.state === "ready" && !queue.projection.refresh_pending) {
      settledSamples += 1;
      if (settledSamples >= 3) return queue;
    } else {
      settledSamples = 0;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
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

async function importFixture(sourceName, pgn, operationId) {
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
  const before = await get("migration/snapshot");
  const beforeReceipt = await get(`operations/${operationId}`);
  assert.equal(beforeReceipt.state, "complete");
  const replay = await importFixture("recovery-study.pgn", studyDurabilityPgn, operationId);
  assert.deepEqual(replay, originalResult, "PGN replay returns the original logical command result");
  const after = await get("migration/snapshot");
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

async function waitForStudyQueue(repertoireId, minimumCardCount) {
  const initialSnapshot = await get("migration/snapshot");
  const repertoireCardIds = new Set((initialSnapshot.tables.repertoire_cards ?? [])
    .filter((row) => row.repertoire_id === repertoireId)
    .map((row) => row.card_id));
  for (const card of initialSnapshot.tables.cards ?? []) {
    if (card.repertoire_id === repertoireId) repertoireCardIds.add(card.id);
  }
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const [queue, integrity] = await Promise.all([
      get("queue/today"), get(`repertoires/${repertoireId}/integrity`),
    ]);
    assertNoCompletedFixtureConflict(integrity, repertoireId);
    const cards = queue.cards.filter((card) => repertoireCardIds.has(card.id));
    if (cards.length >= minimumCardCount) return { queue, snapshot: initialSnapshot, cards };
    if (queue.projection?.state === "failed")
      throw new Error(queue.projection.last_error ?? "PostgreSQL study queue projection failed");
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  const queue = await get("queue/today");
  throw new Error(`PostgreSQL study queue did not admit ${minimumCardCount} entries for ${repertoireId}; `
    + `queue=${queue.cards.filter((card) => repertoireCardIds.has(card.id)).length}, `
    + `projection=${JSON.stringify(queue.projection ?? null)}`);
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
    get("migration/snapshot"), get(`repertoires/${repertoireId}/integrity`),
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
  const snapshot = await get("migration/snapshot");
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
  const coverageRefreshResponse = await apiRequest(
    `repertoires/${importedStudy.repertoire_id}/coverage/refresh`, {
      method: "POST",
      headers: { "Idempotency-Key": `pg-study-coverage-${randomBytes(10).toString("hex")}` },
    }, "foreground POST initial study priority refresh");
  assert.equal(coverageRefreshResponse.status, 202,
    `Coverage priority refresh queues successfully: ${await coverageRefreshResponse.text()}`);
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
  const prefixPreview = await postCommand(`repertoires/${importedStudy.repertoire_id}/canonical-prefix/preview`, {
    movetext: "e4",
  }, { operationId: `pg-study-prefix-preview-${randomBytes(10).toString("hex")}` });
  assert.equal(prefixPreview.state, "checking");
  const repeatedPrefixPreview = await postCommand(`repertoires/${importedStudy.repertoire_id}/canonical-prefix/preview`, {
    movetext: "1. e4",
  }, { operationId: `pg-study-prefix-preview-repeat-${randomBytes(10).toString("hex")}` });
  assert.equal(repeatedPrefixPreview.preview_id, prefixPreview.preview_id,
    "Equivalent independent preview commands reuse the stopped worker's original scan");
  assert((await get("system/tasks")).tasks.some(task => task.kind === "canonical_prefix_preview"
    && task.deduplication_key === prefixPreview.preview_id && ["queued", "retrying"].includes(task.state)),
  "Prefix compatibility remains durable while its worker is stopped");
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

  const prefixDeadline = performance.now() + 30_000;
  let compatibility;
  do {
    compatibility = await get(`repertoires/${importedStudy.repertoire_id}/canonical-prefix/preview/${prefixPreview.preview_id}`);
    if (compatibility.state === "ready") break;
    assert.equal(compatibility.state, "checking", JSON.stringify(compatibility));
    assert(performance.now() < prefixDeadline, "Prefix compatibility completes after worker restart");
    await new Promise(resolve => setTimeout(resolve, 100));
  } while (true);
  const prefixBody = { preview_id: prefixPreview.preview_id, expected_revision: 0 };
  const prefixOperationId = `pg-study-prefix-save-${randomBytes(10).toString("hex")}`;
  const beforePrefixStudy = stableStudyState(await get("migration/snapshot"), importedStudy.repertoire_id);
  const savedPrefix = await postCommand(`repertoires/${importedStudy.repertoire_id}/canonical-prefix`, prefixBody,
    { method: "PUT", operationId: prefixOperationId });
  assert.deepEqual(savedPrefix.moves_uci, ["e2e4"]);
  assert.equal(savedPrefix.revision, 1);
  const beforeBuryQueue = await get("queue/today");
  const buriedCard = beforeBuryQueue.cards[0];
  assert(buriedCard, "Durability fixture includes an active card to bury");
  const beforeBuryState = stableStudyState(await get("migration/snapshot"), buriedCard.repertoire_id);
  const buryOperationId = `pg-study-bury-${randomBytes(12).toString("hex")}`;
  const burial = await postCommand(`queue/entries/${buriedCard.queue_entry_id}/bury`, {}, {
    operationId: buryOperationId, label: "foreground POST bury until tomorrow",
  });
  assert.deepEqual(burial, { buried: true, queue_entry_id: buriedCard.queue_entry_id });
  const afterBuryQueue = await get("queue/today");
  assert.deepEqual(afterBuryQueue.cards.map(card => card.queue_entry_id),
    beforeBuryQueue.cards.filter(card => card.id !== buriedCard.id).map(card => card.queue_entry_id),
    "Bury excludes all active occurrences and preserves other cards' order");

  const studySnapshot = await get("migration/snapshot");
  const afterPrefixStudy = stableStudyState(studySnapshot, importedStudy.repertoire_id);
  for (const field of ["cards", "reviews", "teaching", "annotations", "prefixSplits"])
    assert.deepEqual(afterPrefixStudy[field], beforePrefixStudy[field], `Prefix save preserves ${field}`);
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
  const afterRestartSnapshot = await get("migration/snapshot");
  await verifyPgnImportReplayWithoutDuplicates(pgnImportOperationId, importedStudy, "after recreation");
  await verifyDiscardedPgnReplay(discardedPgnId);
  assert.deepEqual(stableStudyState(afterRestartSnapshot, importedStudy.repertoire_id), beforeRestartState,
    "Authoritative study identities, values, scheduling, annotation, queue, and split survive service recreation");
  const replayedReview = await postCommand(`cards/${reviewCard.id}/review`, reviewBody, {
    operationId: reviewOperationId, label: "foreground replay confirmed review after recreation",
  });
  assert(replayedReview, "Previously confirmed review command replays under its original identity");
  const afterReplaySnapshot = await get("migration/snapshot");
  assert.equal(stableStudyState(afterReplaySnapshot, importedStudy.repertoire_id).reviews.length,
    savedReviewCount, "Confirmed review replay does not create a duplicate business effect");
  assert.deepEqual(await get(`repertoires/${importedStudy.repertoire_id}/canonical-prefix`), savedPrefix,
    "Canonical prefix persists across service recreation");
  const replayedPrefix = await postCommand(`repertoires/${importedStudy.repertoire_id}/canonical-prefix`, prefixBody,
    { method: "PUT", operationId: prefixOperationId });
  assert.deepEqual(replayedPrefix, savedPrefix, "Lost prefix-save acknowledgement replays the original receipt");
  assert.equal((await get(`repertoires/${importedStudy.repertoire_id}/canonical-prefix`)).revision, 1);
  assert((await get("migration/snapshot")).tables.canonical_prefix_positions.some(row => row.preview_id === prefixPreview.preview_id),
    "Verified anchors persist across restart");
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
  // These fixtures have completed their restart/replay proof. Independent Maia
  // results can keep arriving after Explorer fails; release their owned work
  // before the next scenario measures compatibility retention.
  const completedFixtureIds = [importedStudy.repertoire_id, importedBackground.repertoire_id];
  for (const repertoireId of completedFixtureIds) {
    await postCommand(`repertoires/${repertoireId}`, {}, { method: "DELETE" });
  }
  const afterFixtureCleanup = await get("migration/snapshot");
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
  const regression = spawnSync("docker", [...compose, "exec", "-T", "foreground-worker", "python", "-"], {
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
    await new Promise(resolve => setTimeout(resolve, 250));
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
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  assert(before, "Queue settings restoration settles before blocked burial recovery");
  const selected = before.cards[0];
  assert(selected, "Blocked burial fixture has an authoritative active entry");
  const originalState = stableStudyState(await get("migration/snapshot"), selected.repertoire_id);
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
  const afterState = stableStudyState(await get("migration/snapshot"), selected.repertoire_id);
  assert.deepEqual(afterState.cards, originalState.cards, "Blocked recovery leaves scheduling unchanged");
  assert.deepEqual(afterState.reviews, originalState.reviews, "Blocked recovery creates no review");
  assert.deepEqual(await postCommand(`queue/entries/${selected.queue_entry_id}/bury`, {}, { operationId }), result);
  assert.deepEqual((await get("queue/today")).cards.map(card => card.queue_entry_id),
    after.cards.map(card => card.queue_entry_id), "Recovered receipt replay never buries the next card");
  console.log("PASS PostgreSQL blocked burial resumes original payload through retry endpoint without duplicate effects");
}

async function verifyCurrentCanonicalRouteAdmission() {
  const imported = await importFixture("current-canonical-routes.pgn",
    '[Event "Current route"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 *');
  const repertoireId = imported.repertoire_id;
  await waitForStudyableImport(repertoireId);
  const checkCurrentPrefix = async () => {
    const admitted = await postCommand(`repertoires/${repertoireId}/canonical-prefix/preview`,
      { movetext: "e4 e5 Nf3 Nc6 Bc4" });
    const deadline = performance.now() + 30_000;
    while (true) {
      const preview = await get(`repertoires/${repertoireId}/canonical-prefix/preview/${admitted.preview_id}`);
      if (preview.state === "ready") return preview;
      assert.equal(preview.state, "checking", JSON.stringify(preview));
      assert(performance.now() < deadline, "Current-source compatibility finishes");
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  };
  const preview = await checkCurrentPrefix();
  await postCommand(`repertoires/${repertoireId}/canonical-prefix`,
    { preview_id: preview.preview_id, expected_revision: preview.revision }, { method: "PUT" });
  const startingFen = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/2P2N2/PP1P1PPP/RNBQK2R b KQkq - 0 4";
  const continuation = { repertoire_id: repertoireId, name: "Anchored continuation",
    trained_color: "white", starting_fen: startingFen, moves: ["g8f6", "d2d3"] };
  await postCommand("repertoire/branches", continuation);
  await postCommand("repertoire/branches/remove", { repertoire_id: repertoireId,
    starting_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    moves: ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "c2c3"] });
  await assert.rejects(() => postCommand("repertoire/branches",
    { ...continuation, moves: ["d7d6", "d2d3"] }), /outside the repertoire.*canonical prefix/);
  await postCommand("repertoire/branches", { ...continuation, name: "Restored current route",
    starting_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    moves: ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "c2c3"] });
  await waitForStudyableImport(repertoireId);
  const current = await checkCurrentPrefix();
  assert.notEqual(current.preview_id, preview.preview_id,
    "Changed source state requires a fresh computation");
  await postCommand("repertoire/branches", { ...continuation, moves: ["d7d6", "d2d3"] });
  await waitForStudyableImport(repertoireId);
  const candidates = ["", "e4", "e4 e5", "e4 e5 Nf3", "e4 e5 Nf3 Nc6",
    "e4 e5 Nf3 Nc6 Bc4", "e4 e5 Nf3 Nc6 Bc4 Bc5", "e4 e5 Nf3 Nc6 Bc4 Bc5 c3",
    "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6", "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3"];
  const requestedPreviewIds = new Set();
  for (const movetext of candidates) {
    const admitted = await postCommand(`repertoires/${repertoireId}/canonical-prefix/preview`, { movetext });
    requestedPreviewIds.add(admitted.preview_id);
  }
  const retentionDeadline = performance.now() + 30_000;
  while (true) {
    const snapshot = await get("migration/snapshot");
    const previews = snapshot.tables.canonical_prefix_previews.filter(row => row.repertoire_id === repertoireId);
    const pending = (await get("system/tasks")).tasks.some(task => task.kind === "canonical_prefix_preview"
      && requestedPreviewIds.has(task.deduplication_key) && ["queued", "leased", "retrying"].includes(task.state));
    if (previews.length <= 9 && !pending) {
      const repertoire = snapshot.tables.repertoires.find(row => row.id === repertoireId);
      assert(previews.some(row => row.id === repertoire.canonical_prefix_preview_id),
        "Retention preserves the active current certificate");
      break;
    }
    assert(performance.now() < retentionDeadline, "Bounded preview retention finishes and removes abandoned scans");
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  console.log("PASS PostgreSQL deleted-route admission rejects stale proof and accepts a recertified current route");
  console.log("PASS PostgreSQL compatibility retention bounds previews, children, and scan tasks");
}

const actions = {
  compose_config: async () => {
    const config = spawnSync("docker", [...compose, "config", "--format", "json"],
      { encoding: "utf8", env: environment });
    assert.equal(config.status, 0, config.stderr);
    const stack = JSON.parse(config.stdout);
    assert.equal(stack.name, project);
    for (const volumeName of ["postgres-test-data", "redis-test-data", "engine-test-operations"]) {
      assert.equal(stack.volumes[volumeName].external, undefined);
      assert.equal(stack.volumes[volumeName].name, `${project}_${volumeName}`);
    }
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
  },
  image_build: async () => {
    resourcesCreated = true;
    run("docker", [...compose, "build"]);
    // The rehearsal records dependency images before starting its child stack.
    // Standalone mode has no parent startup to pull these images first.
    if (options.mode === "lifecycle") run("docker", [...compose, "pull", "--policy", "missing", "postgres", "redis"]);
  },
  maintenance_cli: async () => {
    run("docker", ["build", "-f", "Dockerfile.postgres-maintenance", "--label", `org.opencontainers.image.revision=${candidateRevision}`, "-t", maintenanceImage, "."]);
    maintenanceImageCreated = true;
    for (const script of ["apply_postgres_migrations.py", "migrate_sqlite_to_postgres.py", "repair_verified_game_tactics.py"]) {
      run("docker", ["run", "--rm", maintenanceImage, `scripts/${script}`, "--help"]);
    }
    console.log("PASS PostgreSQL maintenance image starts migration and import commands");
  },
  startup: async () => {
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
      "background-scheduler", "defense-engine", "maia-worker", "web"])
      assert(runningServices.has(service), `${service} exited during PostgreSQL startup`);
  },
  background_budget: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/check_postgres_background_budget.py"]);
  },
  operation_recovery: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/check_postgres_operation_recovery.py"]);
  },
  schema_migrations: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/check_postgres_upgrade.py"]);
  },
  priority_recovery: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/check_postgres_priority_recovery.py"]);
  },
  background_diagnostics: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/check_postgres_background_diagnostics.py"]);
  },
  deployment_lifecycle: async () => {
    const verifyLifecycle = () => verifyTempoCliLifecycle({ project, environment, revision: candidateRevision,
      composeFiles: [join(process.cwd(), "docker-compose.postgres.test.yml"), buildLabels] });
    if (options.mode === "lifecycle") {
      await verifyLifecycle();
      return;
    }
    const consumers = ["api", "foreground-worker", "background-worker", "background-scheduler", "web", "defense-engine", "maia-worker"];
    run("docker", [...compose, "stop", ...consumers]);
    try {
      await verifyLifecycle();
    } finally {
      run("docker", [...compose, "up", "--no-build", "-d", "--no-deps", ...consumers]);
      await waitForReady();
    }
  },
  background_workloads: async () => {
    await executeIsolatedBackgroundWorkload({
      stopConsumers: () => {
        run("docker", [...compose, "stop", ...workloadConsumers]);
        verifyWorkloadConsumers("exited");
      },
      measureWorkload: () => {
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/scripts/check_postgres_daily_study_dispatch.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
          "/source/scripts/check_postgres_queue_attempt_recovery.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
          "/source/scripts/check_postgres_repertoire_limits.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
          "/source/scripts/check_postgres_deletion.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
          "/source/scripts/check_postgres_tactic_capture.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/scripts/check_postgres_background_workloads.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
          "/source/scripts/check_postgres_defensive_pause.py"]);
        run("docker", [...compose, "exec", "-T", "api", "python", "-c",
          "import os; assert os.environ.get('TEMPO_DATABASE_READ_URL'); assert 'TEMPO_DATABASE_WRITE_URL' not in os.environ; print('PASS deployed prefix API has reader URL and no writer credentials')"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/scripts/check_postgres_opening_segmentation.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/scripts/check_postgres_opening_evidence.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/scripts/check_postgres_canonical_freshness.py"]);
        run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0", "schema", "python",
          "/source/scripts/check_postgres_prefix_transition_application.py"]);
      },
      restoreConsumers: () => restoreBackgroundWorkloadConsumers({
        startConsumers: (services) => run("docker", [...compose, "start", ...services]),
        waitForReady: () => waitForReady({ requireContainerHealthy: true }),
        verifyConsumers: verifyWorkloadConsumers,
      }),
    });
  },
  priority_benchmark: async () => {
    const revision = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
    const dirty = spawnSync("git", ["status", "--porcelain"], { encoding: "utf8" });
    assert.equal(revision.status, 0, revision.stderr);
    assert.equal(dirty.status, 0, dirty.stderr);
    const sourceDirty = dirty.stdout.split("\n").some((line) =>
      line.length > 0 && !/^\?\? \.tempo-pg-test-secrets-[^/]+\/$/.test(line));
    await executeIsolatedBackgroundWorkload({
      stopConsumers: () => {
        run("docker", [...compose, "stop", ...workloadConsumers]);
        verifyWorkloadConsumers("exited");
      },
      measureWorkload: () => {
        run("docker", [...compose, "run", "--rm", "--no-deps",
          "-e", `TEMPO_PRIORITY_BENCHMARK_HEAD=${revision.stdout.trim()}`,
          "-e", `TEMPO_PRIORITY_BENCHMARK_DIRTY=${sourceDirty ? "true" : "false"}`,
          "schema", "python", "/source/scripts/benchmark_postgres_priority.py"]);
      },
      restoreConsumers: () => restoreBackgroundWorkloadConsumers({
        startConsumers: (services) => run("docker", [...compose, "start", ...services]),
        waitForReady: () => waitForReady({ requireContainerHealthy: true }),
        verifyConsumers: verifyWorkloadConsumers,
      }),
    });
  },
  threat_candidate_upsert: async () => {
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/check_postgres_threat_candidate_upsert.py"]);
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
      if ((await get("settings")).new_cards_per_day === updatedSettings.new_cards_per_day) {
        settingsCommitted = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert(settingsCommitted, "Settings business effect commits after its response is discarded");
    run("docker", [...compose, "stop", ...workloadConsumers, "foreground-worker"]);
    runPrefixApplicationProof("--seed-retained");
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
      "/source/scripts/check_postgres_opening_evidence.py", "--verify-persisted"]);
    runPrefixApplicationProof("--verify-retained");
    run("docker", [...compose, "stop", ...workloadConsumers, "foreground-worker"]);
    runPrefixApplicationProof("--recover-retained", "--cleanup-retained");
    run("docker", [...compose, "start", ...workloadConsumers, "foreground-worker"]);
    console.log("PASS PostgreSQL lost-response receipt replay, settings, and queue order survive container recreation");
  },
  backup_restore: async () => {
    run("docker", [...compose, "stop", "api", "foreground-worker", "background-worker",
      "background-scheduler", "defense-engine", "maia-worker", "web"]);
    runPrefixApplicationProof("--seed-retained");
    run("docker", [...compose, "exec", "-T", "postgres", "sh", "-ec",
      "pg_dump -U postgres -d tempo -Fc -f /tmp/tempo-test.dump && " +
      "pg_restore -l /tmp/tempo-test.dump >/dev/null && " +
      "createdb -U postgres tempo_restore_check && " +
      "pg_restore -U postgres -d tempo_restore_check /tmp/tempo-test.dump"]);
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/verify_postgres_backup.py",
      "postgresql://postgres@postgres:5432/tempo",
      "postgresql://postgres@postgres:5432/tempo_restore_check"]);
    run("docker", [...compose, "run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0",
      "-e", "TEMPO_PREFIX_APPLICATION_PROOF_URL=postgresql://postgres@postgres:5432/tempo_restore_check",
      "schema", "python", "/source/scripts/check_postgres_prefix_transition_application.py", "--recover-retained"]);
    runPrefixApplicationProof("--recover-retained", "--cleanup-retained");
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
      run("npx", browserArguments, { env: { ...environment,
        TEMPO_DOCKER_URL: origin,
        TEMPO_TEST_COMPOSE_PROJECT: project,
        TEMPO_TEST_OUTPUT_DIR: join(process.cwd(), "test-results", `browser-postgres-${process.pid}`),
      } });
  },
  study_isolation: async () => {
    run("docker", [...compose, "down", "-v"]);
    run("docker", [...compose, "up", "--no-build", "-d"]);
    await waitForReady();
    assert.equal((await get("queue/today")).cards.length, 0,
      "Fresh durability database starts without browser queue entries");
  },
  study_durability: async () => {
    await verifyForegroundAndStudyDurability();
    await verifyCurrentCanonicalRouteAdmission();
    await verifyStudyBurialRetainsQuota();
    await verifyBlockedBurialRecovery();
    run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/check_postgres_stalemate_swindles.py"]);
  },
  cleanup: async () => executeDiagnosticCleanup(() => {
    if (resourcesCreated) {
      const inspect = (args) => {
        const result = spawnSync("docker", args, { encoding: "utf8", env: environment });
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
        maintenance_image: maintenanceImageCreated ? JSON.parse(inspect(["image", "inspect", maintenanceImage])).map(image => ({ id: image.Id, created: image.Created, tag: maintenanceImage })) : [],
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
      if (resourcesCreated) {
        const stopped = spawnSync("docker", [...compose, "down", "--rmi", "local", "-v"],
          { stdio: "inherit", env: environment });
        if (stopped.error || stopped.status !== 0) cleanupErrors.push(new Error("Disposable PostgreSQL stack cleanup failed"));
        for (const resource of ["container", "volume"]) {
          const remaining = spawnSync("docker", [resource === "container" ? "ps" : "volume", ...(resource === "container" ? ["-aq"] : ["ls", "-q"]),
            "--filter", `label=com.docker.compose.project=${project}`], { encoding: "utf8", env: environment });
          if (remaining.status !== 0 || remaining.stdout.trim()) cleanupErrors.push(new Error(`Disposable PostgreSQL ${resource} cleanup left resources`));
        }
      }
      if (maintenanceImageCreated) {
        const removedMaintenanceImage = spawnSync("docker", ["image", "rm", maintenanceImage],
          { stdio: "ignore", env: environment });
        if (removedMaintenanceImage.error || removedMaintenanceImage.status !== 0)
          cleanupErrors.push(new Error("Disposable maintenance image cleanup failed"));
      }
    } finally {
      rmSync(secretsDirectory, { recursive: true, force: true });
    }
    if (cleanupErrors.length) throw new AggregateError(cleanupErrors, "PostgreSQL resource cleanup failed");
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
