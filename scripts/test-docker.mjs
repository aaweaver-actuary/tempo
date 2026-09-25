import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import assert from "node:assert/strict";
const directory = mkdtempSync(join(tmpdir(), "tempo-docker-tests-"));
const env = { ...process.env, TEMPO_TEST_DATA: directory };
const composeArgs = ["compose", "-p", "tempo-regressions", "-f", "docker-compose.test.yml"];
function run(command, args, extra = {}) {
  const result = spawnSync(command, args, { stdio: "inherit", env: { ...env, ...extra } });
  if (result.error || result.status !== 0) throw new Error(`${command} ${args.join(" ")} failed`);
}
function verifyTempoDataVolumeIsExternal() {
  const result = spawnSync("docker", ["compose", "-f", "docker-compose.yml", "config", "--format", "json"], {
    encoding: "utf8", env,
  });
  if (result.error || result.status !== 0) throw new Error("docker compose config could not resolve docker-compose.yml");
  const composeConfig = JSON.parse(result.stdout);
  assert.equal(composeConfig.volumes?.["tempo-data"]?.external, true, "tempo-data is an external Docker volume");
  assert.equal(composeConfig.volumes["tempo-data"].name, "tempo-data", "tempo-data keeps its existing Docker volume name");
  console.log("PASS test_tempo_data_volume_is_external: Compose uses the existing tempo-data volume.");
}
let exitCode = 0;
const base = "http://127.0.0.1:4180/api";
async function json(path, options) {
  const response = await fetch(`${base}/${path}`, options);
  assert(response.ok, `${path}: HTTP ${response.status}`);
  return response.json();
}
async function waitForHealth() {
  for (let attempt = 0; attempt < 60; attempt++) {
    try { if ((await fetch(`${base}/health`)).ok) return; } catch { /* startup */ }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error("Docker Tempo did not become healthy");
}
async function waitForPublishedQueue() {
  for (let attempt = 0; attempt < 60; attempt++) {
    const queue = await json("queue/today");
    if (queue.projection?.state === "ready" && !queue.projection.refresh_pending) return queue;
    assert.notEqual(queue.projection?.state, "failed", queue.projection?.last_error ?? "Queue projection failed");
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error("Daily queue did not finish refreshing");
}
async function waitForImportSettled(repertoireId) {
  let settledSamples = 0;
  for (let attempt = 0; attempt < 120; attempt++) {
    const system = await json("system/tasks");
    const integrity = await json(`repertoires/${repertoireId}/integrity`);
    const graph = system.tasks.find(task => task.kind === "opening_graph_rebuild" && task.deduplication_key === repertoireId);
    const queueTask = system.tasks.find(task => task.kind === "daily_queue");
    if (graph?.state === "failed" || queueTask?.state === "failed") {
      throw new Error(graph?.last_error ?? queueTask?.last_error ?? "Import derivation failed");
    }
    if (graph?.state === "complete" && integrity.scan_status === "idle" && queueTask?.state === "complete") {
      settledSamples++;
      if (settledSamples >= 3) return waitForPublishedQueue();
    } else {
      settledSamples = 0;
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error("Imported repertoire did not finish its opening graph and queue work");
}
function post(data, method = "POST") { return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }; }

async function verifyStudySurvivesContainerRecreation() {
  const settings = await json("settings");
  await json("settings", post({ ...settings, new_cards_per_day: 100 }, "PUT"));
  const form = new FormData();
  form.set("file", new Blob(['[Event "Container durability"]\n\n1. a3 a6 2. h3 h6 *'], { type: "application/x-chess-pgn" }), "durability.pgn");
  form.set("initial_depth", "2");
  const imported = await json("imports/pgn", { method: "POST", body: form });
  const importedQueue = await waitForImportSettled(imported.repertoire_id);
  const card = importedQueue.cards.find(item => item.repertoire_id === imported.repertoire_id);
  assert(card, "Durability fixture was admitted");
  await json(`cards/${card.id}/teaching`, post({ revision: card.revision, ply: 0 }));
  await json(`repertoires/${imported.repertoire_id}/annotations`, post({ fen: card.start_fen, comment: "Durable failure-only note", arrows: [{ from: "a2", to: "a3", color: "green" }], squares: [] }, "PUT"));
  await json(`cards/${card.id}/review`, post({ outcome: "correct", queue_entry_id: card.queue_entry_id }));
  const reinforcement = (await json("queue/today")).cards.find(item => item.id === card.id);
  assert.equal(reinforcement.attempt_state, "reinforcement");
  await json(`queue/entries/${reinforcement.queue_entry_id}/fail`, { method: "POST" });
  const queueBefore = await waitForImportSettled(imported.repertoire_id);
  const snapshotBefore = await json("migration/snapshot");
  assert(snapshotBefore.counts.reviews > 0 && snapshotBefore.counts.teaching_states > 0 && snapshotBefore.counts.position_annotations > 0);
  run("docker", [...composeArgs, "down"]);
  run("docker", [...composeArgs, "up", "-d"]);
  await waitForHealth();
  const snapshotAfter = await json("migration/snapshot");
  if (snapshotAfter.checksum !== snapshotBefore.checksum) {
    const changedTables = Object.keys(snapshotBefore.tables).filter(name =>
      JSON.stringify(snapshotBefore.tables[name]) !== JSON.stringify(snapshotAfter.tables[name]));
    console.error("Durability snapshot changed tables:", changedTables);
  }
  assert.equal(snapshotAfter.checksum, snapshotBefore.checksum, "All SQLite stores survive container recreation unchanged");
  const queueAfter = await waitForPublishedQueue();
  assert.deepEqual(queueAfter.cards, queueBefore.cards, "Queue order, entry identities, reinforcement and guided state survive recreation");
  assert((await json("queue/today")).cards.find(item => item.id === card.id).fsrs_card_json);
  console.log("PASS Docker study data survives container recreation: every store checksum, queue order, reviews, FSRS, teaching, notes and guided attempts.");
}
try {
  verifyTempoDataVolumeIsExternal();
  run("docker", ["info", "--format", "{{.ServerVersion}}"]);
  run("docker", [...composeArgs, "up", "--build", "-d"]);
  await waitForHealth();
  run("npx", ["playwright", "test"], { TEMPO_DOCKER_URL: "http://127.0.0.1:4180" });
  await verifyStudySurvivesContainerRecreation();
} catch (error) { console.error(error.message); exitCode = 1; }
finally {
  spawnSync("docker", [...composeArgs, "down"], { stdio: "inherit", env });
  rmSync(directory, { recursive: true });
}
process.exit(exitCode);
