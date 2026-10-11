// Run separately from builds/tests: node --expose-gc scripts/benchmark-repertoire-alternatives.mjs [output.json]
import { build } from "esbuild";
import { Chess } from "chess.js";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { performance } from "node:perf_hooks";

const fixtureSeed = 360036;
const samplesPerOperation = 3;
const relevantSources = ["app/lib/repertoire-alternative-index.ts", "app/lib/study-computation.ts",
  "app/lib/position-similarity.ts", "app/utils/canonical-line.ts", "scripts/benchmark-repertoire-alternatives.mjs"];
function git(...args) {
  const result = spawnSync("git", args, { encoding: "utf8" });
  if (result.status !== 0) throw new Error(result.stderr);
  return result.stdout.trim();
}
function summary(samples) {
  const ordered = samples.toSorted((left, right) => left - right);
  return { samples_ms: samples, p50_ms: ordered[Math.ceil(ordered.length / 2) - 1],
    p95_ms: ordered[Math.ceil(ordered.length * 0.95) - 1] };
}
function measure(operation) {
  operation(); // Warm-up; deliberately excluded from samples.
  return summary(Array.from({ length: samplesPerOperation }, () => {
    const start = performance.now(); operation(); return performance.now() - start;
  }));
}
function memory() {
  global.gc();
  return { ...process.memoryUsage(), maxRssBytes: process.resourceUsage().maxRSS * 1024 };
}
function fixture(recordCount, distribution) {
  const duplicateRoute = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6"];
  const distinctRoute = ["a1b1", "h8h7", "b1c1", "h7g7", "c1d1", "g7f7", "d1e1", "f7e7", "e1f1"];
  const lines = [];
  let remainingRecords = recordCount;
  while (remainingRecords) {
    const highDistinctness = distribution === "high-distinctness";
    let startingFen = new Chess().fen();
    if (highDistinctness) {
      // An injective seeded layout: one white pawn per file on ranks 2–5.
      // Both kings' fixed route is legal in every layout. 90% of records are
      // distinct continuation keys, without expensive random-game generation.
      const board = new Chess("7k/8/8/8/8/8/8/K7 w - - 0 1");
      let layout = (Math.imul(lines.length, 40503) + fixtureSeed) & 65535;
      for (let file = 0; file < 8; file++) {
        board.put({ type: "p", color: "w" }, `${String.fromCharCode(97 + file)}${2 + (layout & 3)}`);
        layout >>>= 2;
      }
      startingFen = board.fen();
    }
    const moves = (highDistinctness ? distinctRoute : duplicateRoute).slice(0, remainingRecords - 1);
    lines.push({ id: `line-${lines.length}`, repertoireId: "benchmark-repertoire", repertoireName: "Benchmark",
      title: "Benchmark", side: "white", startingFen, moves });
    remainingRecords -= moves.length + 1;
  }
  return lines;
}

if (process.argv[2] === "--child") {
  if (!global.gc) throw new Error("Measurements require --expose-gc");
  const recordCount = Number(process.argv[3]);
  const distribution = process.argv[4];
  const compiled = await build({ stdin: { contents: [
    'export { computeStudyTask } from "./app/lib/study-computation.ts";',
    'export { buildRepertoireAlternativeSnapshot, lookupRepertoireAlternative } from "./app/lib/repertoire-alternative-index.ts";',
    'export { canonicalFenKey } from "./app/utils/canonical-line.ts";',
  ].join("\n"), resolveDir: process.cwd(), loader: "ts" }, bundle: true, platform: "node", format: "esm", write: false, logLevel: "silent" });
  const { computeStudyTask, buildRepertoireAlternativeSnapshot, lookupRepertoireAlternative, canonicalFenKey } =
    await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString("base64")}`);
  const lines = fixture(recordCount, distribution);
  process.stderr.write("  source preparation\n");
  const preparation = measure(() => computeStudyTask({ kind: "index", lines }));
  const positions = computeStudyTask({ kind: "index", lines });
  if (positions.length !== recordCount) throw new Error(`Expected ${recordCount} records; prepared ${positions.length}`);
  process.stderr.write("  construction, cloning, lookup and memory\n");
  const construction = measure(() => buildRepertoireAlternativeSnapshot(1, positions));
  const beforeIndex = memory();
  const snapshot = buildRepertoireAlternativeSnapshot(1, positions);
  const afterIndex = memory();
  const cloning = measure(() => structuredClone(snapshot));
  const afterCloning = memory();
  const sourceCloning = measure(() => structuredClone(positions));
  const candidates = positions.filter(position => position.nextUci !== undefined);
  const queries = Array.from({ length: 24 }, (_, index) => {
    const position = candidates[Math.floor((candidates.length - 1) * (index + 1) / 24)];
    return { repertoireId: position.repertoireId, fen: position.fen, uci: position.nextUci };
  }).flatMap(query => [query, { ...query, uci: "a1a1" }]);
  function legacyScan(query) {
    return positions.some(position => position.repertoireId === query.repertoireId &&
      canonicalFenKey(position.fen) === canonicalFenKey(query.fen) && position.nextUci === query.uci);
  }
  const expectedResults = queries.map(legacyScan);
  const observedResults = queries.map(query => lookupRepertoireAlternative(snapshot, 1, query.repertoireId, query.fen, query.uci));
  if (JSON.stringify(expectedResults) !== JSON.stringify(observedResults)) throw new Error("Scan/lookup parity failed");
  let checksum = 0;
  const legacyLookup = measure(() => { for (const query of queries) checksum += Number(legacyScan(query)); });
  const indexedLookup = measure(() => { for (const query of queries) checksum += Number(lookupRepertoireAlternative(snapshot, 1, query.repertoireId, query.fen, query.uci)); });
  let distinctKeys = 0, memberships = 0;
  for (const positionsByKey of snapshot.movesByRepertoire.values()) {
    distinctKeys += positionsByKey.size;
    for (const nextMoves of positionsByKey.values()) memberships += nextMoves.size;
  }
  process.stdout.write(JSON.stringify({ distribution, recordCount, lineCount: lines.length,
    repertoireCount: snapshot.movesByRepertoire.size, distinctKeys, memberships,
    queryCount: queries.length, hits: expectedResults.filter(Boolean).length, equivalence: true, checksum,
    preparation, construction, sourceCloning, snapshotCloning: cloning, legacyLookup, indexedLookup,
    memory: { beforeIndex, afterIndex, afterCloning, retainedIndexHeapBytes: afterIndex.heapUsed - beforeIndex.heapUsed,
      final: memory() } }));
} else {
  if (!global.gc) throw new Error("Run with node --expose-gc");
  const startedAt = performance.now();
  const report = { schemaVersion: 1, timestamp: new Date().toISOString(), revision: git("rev-parse", "HEAD"),
    dirtyTree: git("status", "--porcelain").length > 0,
    sourceSha256: createHash("sha256").update(relevantSources.map(path => `${path}\n${readFileSync(path, "utf8")}`).join("\n")).digest("hex"),
    runtime: { node: process.version, platform: process.platform, architecture: process.arch },
    fixtureSeed, samplesPerOperation, fixtures: { highDistinctness: "seeded unique eight-pawn layouts, fixed nine-ply legal king route",
      duplicateHeavy: "repeated eight-ply Spanish route, truncated last route for exact count" },
    method: "Fresh process per exact size/distribution; actual production source preparation and builder; 1 warmup/3 samples. 48 identical paired hit/miss queries per lookup batch. Forced GC retained heap; byte units. No worker scheduling/transfer, UI or persistence. Fixture generation/bundling excluded. Timing and memory descriptive, no pass/fail thresholds.",
    workloads: [] };
  for (const distribution of ["high-distinctness", "duplicate-heavy"]) {
    for (const recordCount of [1000, 10000, 100000]) {
      process.stderr.write(`Measuring ${distribution}, ${recordCount} records\n`);
      const child = spawnSync(process.execPath, ["--expose-gc", import.meta.filename, "--child", String(recordCount), distribution],
        { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"], maxBuffer: 1024 * 1024, timeout: 20 * 60 * 1000 });
      if (child.status !== 0) throw new Error(child.stderr || `Benchmark exited ${child.status}`);
      report.workloads.push(JSON.parse(child.stdout));
    }
  }
  report.wallSeconds = (performance.now() - startedAt) / 1000;
  const outputPath = process.argv[2] ?? "test-results/performance/issue36-repertoire-alternatives.json";
  mkdirSync(dirname(outputPath), { recursive: true }); writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${outputPath}\n`);
}
