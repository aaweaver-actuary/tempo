import { build } from "esbuild";
import { Chess } from "chess.js";
import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { performance } from "node:perf_hooks";

const bundle = await build({
  entryPoints: ["app/lib/study-computation.ts"],
  bundle: true,
  format: "esm",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const moduleUrl = `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`;
const { computeStudyTask } = await import(moduleUrl);
const storeBundle = await build({
  entryPoints: ["app/lib/study-position-store.ts"], bundle: true, format: "esm",
  platform: "node", write: false, logLevel: "silent",
});
const storeModuleUrl = `data:text/javascript;base64,${Buffer.from(storeBundle.outputFiles[0].text).toString("base64")}`;
const { createStudyPositionStore } = await import(storeModuleUrl);
const canonicalBundle = await build({
  entryPoints: ["app/utils/canonical-line.ts"], bundle: true, format: "esm",
  platform: "node", write: false, logLevel: "silent",
});
const canonicalModuleUrl = `data:text/javascript;base64,${Buffer.from(canonicalBundle.outputFiles[0].text).toString("base64")}`;
const { canonicalizeLine } = await import(canonicalModuleUrl);
const startFen = new Chess().fen();
const movePairs = new Chess().moves({ verbose: true }).flatMap((firstMove) => {
  const board = new Chess();
  board.move(firstMove);
  return board.moves({ verbose: true }).map((reply) => [
    `${firstMove.from}${firstMove.to}${firstMove.promotion ?? ""}`,
    `${reply.from}${reply.to}${reply.promotion ?? ""}`,
  ]);
});
const uniqueOpeningRoutes = movePairs.flatMap((pair) => {
  const board = new Chess();
  for (const uci of pair)
    board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] || undefined });
  return board.moves({ verbose: true }).map((thirdMove) => [
    ...pair, `${thirdMove.from}${thirdMove.to}${thirdMove.promotion ?? ""}`,
  ]);
});
const workloads = [
  { name: "small", lines: 25, samples: 5 },
  { name: "typical", lines: 250, samples: 5 },
  { name: "large", lines: 2000, samples: 5 },
  { name: "stress", lines: 8000, samples: 3 },
];
function summary(samples) {
  const sorted = samples.toSorted((left, right) => left - right);
  return { p50: sorted[Math.ceil(sorted.length / 2) - 1], p95: sorted[Math.ceil(sorted.length * 0.95) - 1] };
}
function measure(operation, sampleCount) {
  operation();
  const samples = [];
  for (let sample = 0; sample < sampleCount; sample++) {
    const startedAt = performance.now();
    operation();
    samples.push(Number((performance.now() - startedAt).toFixed(3)));
  }
  return { samples_ms: samples, ...summary(samples) };
}
const commitResult = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
const report = {
  schema_version: 1,
  commit: commitResult.status === 0 ? commitResult.stdout.trim() : null,
  timestamp: new Date().toISOString(),
  environment: { platform: process.platform, architecture: process.arch, node: process.version },
  fixture: "unique-legal-three-ply-opening-routes-v2",
  method: "In-process study computation; excludes worker transfer, UI, and persistence",
  workloads: {},
};
for (const workload of workloads) {
  const lines = Array.from({ length: workload.lines }, (_, index) => ({
    id: `line-${index}`,
    repertoireId: "benchmark-repertoire",
    repertoireName: "Benchmark repertoire",
    title: `Line ${index}`,
    side: "white",
    startingFen: startFen,
    moves: uniqueOpeningRoutes[index],
  }));
  if (lines.some((line) => !line.moves))
    throw new Error(`The ${workload.name} position workload exceeds the unique legal route fixture.`);
  const indexTiming = measure(() => computeStudyTask({ kind: "index", lines }), workload.samples);
  const canonicalLines = lines.map(canonicalizeLine);
  const workerInitializeTiming = measure(() => {
    const run = createStudyPositionStore();
    run({
      kind: "initializePositionIndex", repertoireId: "benchmark-repertoire",
      revision: 1, lines: canonicalLines,
    });
  }, workload.samples);
  const positions = computeStudyTask({ kind: "index", lines });
  const searchTask = { kind: "matches", fen: startFen, positions };
  const searchTiming = measure(() => computeStudyTask(searchTask), workload.samples);
  const queryCloneTiming = measure(() => structuredClone(searchTask), workload.samples);
  const compactQueryTask = {
    kind: "findPositionMatches", repertoireId: "benchmark-repertoire",
    revision: 1, fen: startFen, limit: 8,
  };
  const compactQueryCloneTiming = measure(() => structuredClone(compactQueryTask), workload.samples);
  const resultCloneTiming = measure(() => structuredClone(positions), workload.samples);
  const matches = computeStudyTask(searchTask);
  report.workloads[workload.name] = {
    lines: lines.length,
    plies: lines.length * 3,
    indexed_positions: positions.length,
    distinct_fens: new Set(positions.map((position) => position.fen)).size,
    result_count: matches.length,
    query_bytes_json: Buffer.byteLength(JSON.stringify(searchTask)),
    compact_query_bytes_json: Buffer.byteLength(JSON.stringify(compactQueryTask)),
    index: indexTiming,
    worker_index_initialize: workerInitializeTiming,
    matches: searchTiming,
    query_clone: queryCloneTiming,
    compact_query_clone: compactQueryCloneTiming,
    index_result_clone: resultCloneTiming,
  };
}
const outputDirectory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
mkdirSync(outputDirectory, { recursive: true });
const outputPath = `${outputDirectory}/study-position-benchmark.json`;
writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`);
console.log(outputPath);
for (const [name, workload] of Object.entries(report.workloads))
  console.log(`${name}: ${workload.index.p50.toFixed(1)} ms index, ${workload.matches.p50.toFixed(1)} ms matches, ${workload.query_clone.p50.toFixed(1)} ms query clone`);
