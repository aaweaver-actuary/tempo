import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const baseUrl = process.env.TEMPO_PHONE_URL ?? "http://127.0.0.1:3000";
const expectedServiceWorker = await readFile(new URL("../public/sw.js", import.meta.url), "utf8");
const expectedCacheVersion = expectedServiceWorker.match(/const CACHE = "([^"]+)"/)?.[1];
assert(expectedCacheVersion, "Local service worker has no cache version");

async function get(path) {
  const response = await fetch(new URL(path, baseUrl), { signal: AbortSignal.timeout(15_000) });
  assert(response.ok, `${path} returned HTTP ${response.status}; rebuild both Tempo web and API services`);
  return response;
}

const preparedQueue = await (await get("/api/queue/prepared")).json();
assert(typeof preparedQueue.prepared_at === "string", "Running API does not provide a prepared phone queue");
assert(typeof preparedQueue.local_date === "string", "Prepared queue has no study date");
assert(preparedQueue.projection?.state === "ready", "Prepared queue is not ready");
assert(Array.isArray(preparedQueue.cards) && preparedQueue.count === preparedQueue.cards.length,
  "Prepared queue does not contain every card");

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

console.log(`Phone deployment ready: ${expectedCacheVersion}, ${preparedQueue.count} cards for ${preparedQueue.local_date}`);
