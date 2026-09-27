import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";

const baseUrl = process.env.TEMPO_PHONE_URL ?? "http://127.0.0.1:3000";
const directApiUrl = process.env.TEMPO_PHONE_API_URL ?? "http://127.0.0.1:8000";
const expectedServiceWorker = await readFile(new URL("../public/sw.js", import.meta.url), "utf8");
const expectedCacheVersion = expectedServiceWorker.match(/const CACHE = "([^"]+)"/)?.[1];
assert(expectedCacheVersion, "Local service worker has no cache version");

async function get(path) {
  const response = await fetch(new URL(path, baseUrl), { signal: AbortSignal.timeout(15_000) });
  assert(response.ok, `${path} returned HTTP ${response.status}; rebuild both Tempo web and API services`);
  return response;
}

const directHealth = await fetch(new URL("/api/health", directApiUrl), { signal: AbortSignal.timeout(15_000) });
assert(directHealth.ok, `Direct Tempo API health returned HTTP ${directHealth.status}`);
for (const path of ["/api/health", "/api/settings", "/api/repertoire/lines",
  "/api/tactics/progress", "/api/games/sync/status"]) {
  const response = await get(path);
  await response.body?.cancel();
}

async function matchingQueuePair() {
  let preparedQueue;
  let queueWindow;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    preparedQueue = await (await get("/api/queue/prepared")).json();
    queueWindow = await (await get("/api/queue/window?limit=20")).json();
    if (preparedQueue.count === queueWindow.count
      && preparedQueue.projection?.generation === queueWindow.projection?.generation
      && JSON.stringify(queueWindow.cards.map((card) => card.queue_entry_id))
        === JSON.stringify(preparedQueue.cards.slice(0, 20).map((card) => card.queue_entry_id))) {
      return { preparedQueue, queueWindow };
    }
  }
  assert.deepEqual(queueWindow.cards.map((card) => card.queue_entry_id),
    preparedQueue.cards.slice(0, 20).map((card) => card.queue_entry_id),
    "Live queue window differs from the prepared queue after three reads");
  assert.equal(queueWindow.count, preparedQueue.count, "Live queue and prepared queue counts differ");
  assert.equal(queueWindow.projection?.generation, preparedQueue.projection?.generation,
    "Live queue and prepared queue generations differ");
  throw new Error("Live queue window changed during verification");
}

const { preparedQueue } = await matchingQueuePair();
assert(typeof preparedQueue.prepared_at === "string", "Running API does not provide a prepared phone queue");
assert(typeof preparedQueue.local_date === "string", "Prepared queue has no study date");
assert(preparedQueue.projection?.state === "ready", "Prepared queue is not ready");
assert(Array.isArray(preparedQueue.cards) && preparedQueue.count === preparedQueue.cards.length,
  "Prepared queue does not contain every card");
const schemaBundle = await build({
  entryPoints: [new URL("../app/domain/schemas/index.ts", import.meta.url).pathname],
  bundle: true, format: "esm", platform: "node", write: false,
});
const { queueCardSchema } = await import(`data:text/javascript;base64,${Buffer.from(schemaBundle.outputFiles[0].contents).toString("base64")}`);
for (const [index, card] of preparedQueue.cards.entries()) {
  const result = queueCardSchema.safeParse(card);
  assert(result.success,
    `Prepared card ${index + 1} (${card?.id ?? "unknown"}) differs from the web queue contract: ${JSON.stringify(result.error?.issues)}`);
}
const runningServiceWorker = await (await get("/sw.js")).text();
assert(runningServiceWorker.includes(`const CACHE = "${expectedCacheVersion}"`),
  `Running web service worker does not match ${expectedCacheVersion}`);
assert(runningServiceWorker.includes("tempo:offline-shell-status"),
  "Running web service worker cannot confirm its offline shell");
const html = await (await get("/")).text();
const scriptPath = html.match(/<script[^>]+src="([^"]+\.js)"/)?.[1];
assert(scriptPath, "Running web page has no built JavaScript entry");
const script = await (await get(scriptPath)).text();
assert(script.includes("Phone queue prepared for"), "Running web bundle lacks phone queue preparation");
assert(script.includes(expectedCacheVersion), "Running web bundle and service worker versions differ");
if (preparedQueue.cards.some((card) => Object.hasOwn(card, "study_exercise_id")))
  assert(script.includes("study_exercise_id"), "Running web bundle does not support study exercise queue metadata");

console.log(`Phone deployment ready: ${expectedCacheVersion}, ${preparedQueue.count} cards for ${preparedQueue.local_date}`);
