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
function post(data, method = "POST") { return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }; }

async function verifyStudySurvivesContainerRecreation() {
  const settings = await json("settings");
  await json("settings", post({ ...settings, new_cards_per_day: 100 }, "PUT"));
  const form = new FormData();
  form.set("file", new Blob(['[Event "Container durability"]\n\n1. a3 a6 2. h3 h6 *'], { type: "application/x-chess-pgn" }), "durability.pgn");
  form.set("initial_depth", "2");
  const imported = await json("imports/pgn", { method: "POST", body: form });
  const card = (await json("queue/today")).cards.find(item => item.repertoire_id === imported.repertoire_id);
  assert(card, "Durability fixture was admitted");
  await json(`cards/${card.id}/teaching`, post({ revision: card.revision, ply: 0 }));
  await json(`repertoires/${imported.repertoire_id}/annotations`, post({ fen: card.start_fen, comment: "Durable failure-only note", arrows: [{ from: "a2", to: "a3", color: "green" }], squares: [] }, "PUT"));
  await json(`cards/${card.id}/review`, post({ outcome: "correct", queue_entry_id: card.queue_entry_id }));
  const reinforcement = (await json("queue/today")).cards.find(item => item.id === card.id);
  assert.equal(reinforcement.attempt_state, "reinforcement");
  await json(`queue/entries/${reinforcement.queue_entry_id}/fail`, { method: "POST" });
  const queueBefore = await json("queue/today");
  const snapshotBefore = await json("migration/snapshot");
  assert(snapshotBefore.counts.reviews > 0 && snapshotBefore.counts.teaching_states > 0 && snapshotBefore.counts.position_annotations > 0);
  run("docker", [...composeArgs, "down"]);
  run("docker", [...composeArgs, "up", "-d"]);
  await waitForHealth();
  const snapshotAfter = await json("migration/snapshot");
  assert.equal(snapshotAfter.checksum, snapshotBefore.checksum, "All SQLite stores survive container recreation unchanged");
  assert.deepEqual(await json("queue/today"), queueBefore, "Queue order, entry identities, reinforcement and guided state survive recreation");
  assert((await json("queue/today")).cards.find(item => item.id === card.id).fsrs_card_json);
  console.log("PASS Docker study data survives container recreation: every store checksum, queue order, reviews, FSRS, teaching, notes and guided attempts.");
}
try {
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
