// Disposable PostgreSQL product and container-recreation gate.

import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createIsolatedTestEnvironment } from "./test-environment.mjs";
import { buildPostgresPlaywrightArguments, parsePostgresTestOptions } from "./postgres-test-options.mjs";

const options = parsePostgresTestOptions(process.argv.slice(2));
const { skipBrowser } = options;
if (options.list) {
  console.log(JSON.stringify({ stages: ["compose_config", "maintenance_cli", "startup", "command_receipt",
    "container_recreation", "backup_restore", ...(skipBrowser ? [] : ["browser"]),
    "cleanup"], browser_file: options.browserFile, browser_grep: options.browserGrep }, null, 2));
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
  TEMPO_POSTGRES_ADMIN_PASSWORD_FILE: join(secretsDirectory, "admin_password"),
  TEMPO_POSTGRES_READER_PGPASS_FILE: join(secretsDirectory, "reader_pgpass"),
  TEMPO_POSTGRES_WRITER_PGPASS_FILE: join(secretsDirectory, "writer_pgpass"),
  TEMPO_POSTGRES_ADMIN_PGPASS_FILE: join(secretsDirectory, "admin_pgpass"),
});
const origin = `http://127.0.0.1:${testPort}`;
let resourcesCreated = false;
const measuredHttpRequests = [];

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

async function waitForReady() {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    try {
      const response = await fetch(`${origin}/api/health`, { signal: AbortSignal.timeout(3_000) });
      if (response.ok) {
        const health = await response.json();
        assert.equal(health.storage, "postgresql");
        assert.equal(health.test_instance, true);
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

async function waitForImportSettled(repertoireId) {
  let settledSamples = 0;
  for (let attempt = 0; attempt < 180; attempt += 1) {
    const [system, integrity, queue] = await Promise.all([
      get("system/tasks"), get(`repertoires/${repertoireId}/integrity`), get("queue/today"),
    ]);
    const graphTask = system.tasks.find((task) => task.kind === "opening_graph_rebuild"
      && task.deduplication_key === repertoireId);
    if (graphTask?.state === "failed" || integrity.scan_status === "failed"
      || queue.projection?.state === "failed") {
      throw new Error(graphTask?.last_error ?? integrity.last_error
        ?? queue.projection?.last_error ?? "PostgreSQL import background work failed");
    }
    if (graphTask?.state === "complete" && integrity.scan_status === "idle"
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
    prefixSplits: selectRows("prefix_splits", (row) => cardIds.has(row.source_card_id), [
      "source_card_id", "source_revision", "shortened_card_id", "continuation_card_id",
    ]),
  };
}

async function importFixture(sourceName, pgn) {
  const form = new FormData();
  form.set("file", new Blob([pgn], { type: "application/x-chess-pgn" }), sourceName);
  form.set("trained_color", "white");
  form.set("initial_depth", "6");
  const imported = await confirm(await apiRequest("imports/pgn", { method: "POST", body: form },
    `foreground POST imports/pgn (${sourceName})`));
  assert(imported.repertoire_id, "PGN import returns the persisted repertoire identity");
  return imported;
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
    const queue = await get("queue/today");
    const cards = queue.cards.filter((card) => repertoireCardIds.has(card.id));
    if (cards.length >= minimumCardCount) return { queue, snapshot: initialSnapshot, cards };
    if (queue.projection?.state === "failed")
      throw new Error(queue.projection.last_error ?? "PostgreSQL study queue projection failed");
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  const queue = await get("queue/today");
  const priority = (initialSnapshot.tables.repertoire_priority_jobs ?? [])
    .find((row) => row.repertoire_id === repertoireId);
  throw new Error(`PostgreSQL study queue did not admit ${minimumCardCount} entries for ${repertoireId}; `
    + `queue=${queue.cards.filter((card) => repertoireCardIds.has(card.id)).length}, `
    + `priority=${JSON.stringify(priority ?? null)}, projection=${JSON.stringify(queue.projection ?? null)}`);
}

function seedImportedCardsIntoDisposableStudyQueue(repertoireId) {
  assert.match(repertoireId, /^[0-9a-f-]{36}$/i, "Imported repertoire ID is a UUID");
  // Integrity diagnostics are covered independently; this exercise holds the
  // imported opening fixture eligible so persistence commands can use its cards.
  const sql = `DELETE FROM repertoire_integrity_card_blocks WHERE repertoire_id='${repertoireId}';
  UPDATE repertoire_integrity_state SET status='clean',scan_status='idle',scan_error=NULL
    WHERE repertoire_id='${repertoireId}';
  UPDATE cards SET pending_validation=0
    WHERE repertoire_id='${repertoireId}' OR id IN (
      SELECT card_id FROM repertoire_cards WHERE repertoire_id='${repertoireId}'
    );
  WITH candidates AS (
    SELECT cards.id AS card_id,
           COALESCE((SELECT MAX(queued.cycle)+1 FROM daily_queue queued
                     WHERE queued.queue_date=CURRENT_DATE::text AND queued.card_id=cards.id),0) AS next_cycle,
           row_number() OVER (ORDER BY cards.id) AS offset
    FROM cards
    WHERE cards.archived=0 AND jsonb_array_length(cards.moves_json::jsonb)>=3
      AND (cards.repertoire_id='${repertoireId}' OR cards.id IN (
        SELECT card_id FROM repertoire_cards WHERE repertoire_id='${repertoireId}'
      ))
    ORDER BY cards.id
    LIMIT 3
  ), current_position AS (
    SELECT COALESCE(MAX(position), -1) AS maximum FROM daily_queue
    WHERE queue_date=CURRENT_DATE::text
  )
  INSERT INTO daily_queue(queue_date,card_id,cycle,position,admission_kind,admission_repertoire_id)
  SELECT CURRENT_DATE::text,candidates.card_id,candidates.next_cycle,current_position.maximum+candidates.offset,
         'explicit','${repertoireId}'
  FROM candidates CROSS JOIN current_position
  ON CONFLICT(queue_date,card_id,cycle) DO NOTHING;`;
  run("docker", [...compose, "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo",
    "-v", "ON_ERROR_STOP=1", "-c", sql]);
}

async function verifyForegroundAndStudyDurability() {
  const studySettings = await get("settings");
  await postCommand("settings", { ...studySettings, new_cards_per_day: 100, study_new_per_day: 100 }, {
    method: "PUT", label: "foreground PUT study queue allowance",
  });
  const studyPgn = [
    '[Event "PostgreSQL recovery durability"]', "",
    "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 d6 5. O-O Be7 6. c3 O-O 7. Re1 a6 8. Bb3 *",
    '[Event "Second deterministic line"]', "",
    "1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. Nf3 Be7 5. Bg5 O-O 6. e3 h6 7. Bh4 b6 8. cxd5 *", "",
  ].join("\n");
  const importedStudy = await importFixture("recovery-study.pgn", studyPgn);
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
  await waitForImportSettled(importedStudy.repertoire_id);
  seedImportedCardsIntoDisposableStudyQueue(importedStudy.repertoire_id);
  const { cards: studyCards } = await waitForStudyQueue(importedStudy.repertoire_id, 2);
  const reviewCard = studyCards[0];
  const splitCard = studyCards.find((card) => card.id !== reviewCard.id && card.moves.length >= 3);
  const guidedCard = splitCard;
  assert(splitCard, "Study fixture includes a separate multi-move prefix-split entry");

  run("docker", [...compose, "stop", "background-worker"]);
  const backgroundStudyPgn = [
    '[Event "Queued background publication"]', "",
    "1. c4 e5 2. Nc3 Nf6 3. g3 d5 4. cxd5 Nxd5 *", "",
  ].join("\n");
  const importedBackground = await importFixture("background-publication.pgn", backgroundStudyPgn);
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
  await waitForImportSettled(importedStudy.repertoire_id);
  await waitForImportSettled(importedBackground.repertoire_id);
  const savedTeaching = await get(`cards/${reviewCard.id}/teaching`);
  assert(savedTeaching.states.some((state) => state.revision === reviewCard.revision && state.ply === 0));
  const savedAnnotations = await get(`repertoires/${importedStudy.repertoire_id}/annotations?fen=${encodeURIComponent(reviewCard.start_fen)}`);
  assert(savedAnnotations.annotations.some((item) => item.comment === annotation.comment
    && item.arrows.length === 1 && item.squares.length === 1));
  const reinforcementQueue = await get("queue/today");
  const reinforcement = reinforcementQueue.cards.find((card) => card.id === reviewCard.id);
  assert.equal(reinforcement?.attempt_state, "reinforcement");

  const studySnapshot = await get("migration/snapshot");
  assert.equal(studySnapshot.source, "tempo-postgres");
  assert(studySnapshot.counts.reviews > 0 && studySnapshot.counts.teaching_states > 0
    && studySnapshot.counts.position_annotations > 0 && studySnapshot.counts.prefix_splits > 0);
  const beforeRestartState = stableStudyState(studySnapshot, importedStudy.repertoire_id);
  assert(beforeRestartState.reviews.some((row) => row.card_id === reviewCard.id));
  assert(beforeRestartState.queue.some((row) => row.card_id === guidedCard.id
    && (row.attempt_failed === 1 || row.attempt_state === "failed" || row.attempt_state === "guided")));
  assert(beforeRestartState.prefixSplits.some((row) => row.source_card_id === splitCard.id));

  const savedReviewCount = beforeRestartState.reviews.length;
  run("docker", [...compose, "down"]);
  run("docker", [...compose, "up", "-d"]);
  await waitForReady();
  await waitForImportSettled(importedStudy.repertoire_id);
  await waitForImportSettled(importedBackground.repertoire_id);
  const afterRestartSnapshot = await get("migration/snapshot");
  assert.deepEqual(stableStudyState(afterRestartSnapshot, importedStudy.repertoire_id), beforeRestartState,
    "Authoritative study identities, values, scheduling, annotation, queue, and split survive service recreation");
  const replayedReview = await postCommand(`cards/${reviewCard.id}/review`, reviewBody, {
    operationId: reviewOperationId, label: "foreground replay confirmed review after recreation",
  });
  assert(replayedReview, "Previously confirmed review command replays under its original identity");
  const afterReplaySnapshot = await get("migration/snapshot");
  assert.equal(stableStudyState(afterReplaySnapshot, importedStudy.repertoire_id).reviews.length,
    savedReviewCount, "Confirmed review replay does not create a duplicate business effect");
  console.log("PASS PostgreSQL study state, queue order, guided failure, and command identity survive service recreation");
}

let failed = false;
try {
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
  resourcesCreated = true;
  run("docker", ["build", "-f", "Dockerfile.postgres-maintenance", "-t", maintenanceImage, "."]);
  for (const script of ["apply_postgres_migrations.py", "migrate_sqlite_to_postgres.py"]) {
    run("docker", ["run", "--rm", maintenanceImage, `scripts/${script}`, "--help"]);
  }
  console.log("PASS PostgreSQL maintenance image starts migration and import commands");
  run("docker", [...compose, "up", "--build", "-d"]);
  await waitForReady();
  run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
    "/source/scripts/check_postgres_background_budget.py"]);
  run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
    "/source/scripts/check_postgres_operation_recovery.py"]);
  run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
    "/source/scripts/check_postgres_upgrade.py"]);
  run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
    "/source/scripts/check_postgres_background_workloads.py"]);
  run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
    "/source/scripts/check_postgres_threat_candidate_upsert.py"]);
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
  const settings = await get("settings");
  const before = await get("queue/today");
  const operationId = `pg-durability-${randomBytes(12).toString("hex")}`;
  const updatedSettings = { ...settings, new_cards_per_day: settings.new_cards_per_day + 1 };
  const sendSettings = () => apiRequest("settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": operationId },
    body: JSON.stringify(updatedSettings),
  });
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
  run("docker", [...compose, "down"]);
  run("docker", [...compose, "up", "-d"]);
  await waitForReady();
  assert.equal((await get("settings")).new_cards_per_day, updatedSettings.new_cards_per_day);
  await confirm(await sendSettings());
  const receipt = await get(`operations/${operationId}`);
  assert.equal(receipt.state, "complete");
  const after = await get("queue/today");
  assert.deepEqual(after.cards.map(card => card.queue_entry_id),
    before.cards.map(card => card.queue_entry_id));
  console.log("PASS PostgreSQL lost-response receipt replay, settings, and queue order survive container recreation");
  run("docker", [...compose, "stop", "api", "foreground-worker", "background-worker",
    "background-scheduler", "defense-engine", "maia-worker", "web"]);
  run("docker", [...compose, "exec", "-T", "postgres", "sh", "-ec",
    "pg_dump -U postgres -d tempo -Fc -f /tmp/tempo-test.dump && " +
    "pg_restore -l /tmp/tempo-test.dump >/dev/null && " +
    "createdb -U postgres tempo_restore_check && " +
    "pg_restore -U postgres -d tempo_restore_check /tmp/tempo-test.dump"]);
  run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
    "/source/scripts/verify_postgres_backup.py",
    "postgresql://postgres@postgres:5432/tempo",
    "postgresql://postgres@postgres:5432/tempo_restore_check"]);
  run("docker", [...compose, "exec", "-T", "postgres", "sh", "-ec",
    "dropdb -U postgres tempo_restore_check && rm /tmp/tempo-test.dump"]);
  console.log("PASS every PostgreSQL table matches after backup restoration");
  run("docker", [...compose, "up", "-d"]);
  await waitForReady();
  if (!skipBrowser) {
    const browserArguments = buildPostgresPlaywrightArguments(options);
    run("npx", browserArguments, { env: { ...environment,
      TEMPO_DOCKER_URL: origin,
      TEMPO_TEST_OUTPUT_DIR: join(process.cwd(), "test-results", `browser-postgres-${process.pid}`),
    } });
  }
  await verifyForegroundAndStudyDurability();
} catch (error) {
  failed = true;
  console.error(error);
  spawnSync("docker", [...compose, "logs", "--tail=80"], { stdio: "inherit", env: environment });
} finally {
  if (resourcesCreated) {
    const stopped = spawnSync("docker", [...compose, "down", "--rmi", "local", "-v"],
      { stdio: "inherit", env: environment });
    if (stopped.status !== 0) failed = true;
    const removedMaintenanceImage = spawnSync("docker", ["image", "rm", maintenanceImage],
      { stdio: "ignore", env: environment });
    if (removedMaintenanceImage.error) failed = true;
  }
  rmSync(secretsDirectory, { recursive: true, force: true });
}
process.exit(failed ? 1 : 0);
