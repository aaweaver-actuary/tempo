// Offline bounded measurement; run serially, independently of other validation.
import { readFile } from "node:fs/promises";
import { performance } from "node:perf_hooks";
import * as ort from "onnxruntime-web/wasm";
import { inferMaiaDistribution, initializeMaiaInference } from "./maia-inference.mjs";

const fixtures = JSON.parse(await readFile(new URL("../tests/fixtures/maia-probability/examples.json", import.meta.url), "utf8"));
const selected = ["opening-white", "opening-black", "black-castling", "black-en-passant", "black-promotion"]
  .map(name => fixtures.cases.find(fixture => fixture.name === name));
const ratings = { side_to_move_rating: 1500, other_player_rating: 1600 };
const roundCount = 5;
let inferenceCount = 0;
async function measure(fixture) {
  const started = performance.now();
  const result = await inferMaiaDistribution({ position: fixture.position, ratings,
    expected_side_to_move: fixture.position.fen_key.split(" ")[1] === "w" ? "white" : "black" });
  inferenceCount += 1;
  if (!result.moves || result.moves.length !== fixture.position.legal_moves.length)
    throw new Error(`Incomplete benchmark result: ${fixture.name}`);
  return performance.now() - started;
}
const cpuStart = process.cpuUsage();
const initializationStarted = performance.now();
await initializeMaiaInference();
const coldInitializationMilliseconds = performance.now() - initializationStarted;
const coldMilliseconds = await measure(selected[0]);
const warmMilliseconds = [];
for (let round = 0; round < roundCount; round += 1)
  for (const fixture of selected) warmMilliseconds.push(await measure(fixture));
const sorted = warmMilliseconds.toSorted((left, right) => left - right);
const percentile = fraction => sorted[Math.ceil(sorted.length * fraction) - 1];
const usage = process.cpuUsage(cpuStart);
const peakMemoryMiB = process.resourceUsage().maxRSS / 1024;
if (ort.env.wasm.numThreads !== 1 || peakMemoryMiB >= 768) throw new Error("Maia exceeds the existing thread/memory budget");
console.log(JSON.stringify({ schema_version: 1, platform: process.platform, architecture: process.arch,
  node: process.version, runtime: ort.env.versions.web, threads: ort.env.wasm.numThreads,
  fixtures: selected.map(fixture => fixture.name), ratings, inference_count: inferenceCount,
  cold_initialization_milliseconds: coldInitializationMilliseconds,
  first_inference_milliseconds: coldMilliseconds, warm_count: warmMilliseconds.length,
  warm_p50_milliseconds: percentile(.5), warm_p95_milliseconds: percentile(.95),
  warm_max_milliseconds: sorted.at(-1), cpu_user_milliseconds: usage.user / 1000,
  cpu_system_milliseconds: usage.system / 1000, peak_process_memory_mib: peakMemoryMiB }, null, 2));
