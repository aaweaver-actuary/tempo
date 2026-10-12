import { expect, test } from "./observability";
import { build } from "esbuild";
import { readFile } from "node:fs/promises";
import { writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { relative } from "node:path";

// Only the worker asset URL is rebound to a Blob. Dispatcher, computation,
// position store, validation and cancellation are the actual product modules.
async function nativeHarness() {
  const sourcePlugin = { name: "native-worker-source", setup(builder: import("esbuild").PluginBuild) {
    builder.onLoad({ filter: /\/app\/.*\.tsx?$/ }, async args => {
      const contents = process.env.TEMPO_ISSUE190_SHARED_BASELINE === "1"
        ? execFileSync("git", ["show", `3f7332399796366eb32f9fcb5e1b5c678a5b1562:${relative(process.cwd(), args.path)}`], { encoding: "utf8" })
        : await readFile(args.path, "utf8");
      return { contents: contents.replaceAll('new URL("./study.worker.ts", import.meta.url)', 'globalThis.tempoNativeWorkerUrl'),
        loader: args.path.endsWith(".tsx") ? "tsx" as const : "ts" as const };
    });
  } };
  const worker = await build({ entryPoints: ["app/lib/study.worker.ts"], bundle: true, write: false, format: "iife", plugins: [sourcePlugin] });
  const dispatcher = await build({ stdin: { contents: `import { runStudyTask } from './app/lib/background-study';
    import { browserActivitySnapshot } from './app/lib/browser-activity';
    globalThis.tempoNative = { runStudyTask, browserActivitySnapshot };`, resolveDir: process.cwd() },
    bundle: true, write: false, format: "iife", plugins: [sourcePlugin] });
  return `globalThis.tempoNativeWorkerUrl = URL.createObjectURL(new Blob([${JSON.stringify(worker.outputFiles[0].text)}], {type:'text/javascript'}));\n${dispatcher.outputFiles[0].text}`;
}

test("issue190_native_cancelled_bulk_releases_foreground_capacity_within_500ms", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.addScriptTag({ content: await nativeHarness() });
  const result = await page.evaluate(async () => {
    const native = Reflect.get(globalThis, "tempoNative");
    const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    const lines = Array.from({ length: 8268 }, (_, index) => ({ id: `public-${index}`, repertoireId: `public-${index % 10}`,
      repertoireName: "Public capacity", title: `Line ${index}`, side: "white", startingFen,
      moves: Array.from({ length: 8 }, () => ["g1f3", "g8f6", "f3g1", "f6g8"]).flat() }));
    await native.runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } });
    const cancellation = new AbortController();
    const settled = native.runStudyTask({ kind: "repertoireAlternatives", generation: 1, lines }, cancellation.signal).catch((error: Error) => error.name);
    const deadline = performance.now() + 10_000;
    while (!native.browserActivitySnapshot().some((item: { title: string; state: string }) => item.title === "Preparing repertoire lines" && item.state === "running")) {
      if (performance.now() > deadline) throw new Error("Native repertoire worker never started");
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    }
    cancellation.abort();
    const cancelled = await settled;
    const started = performance.now();
    const foreground = await native.runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } });
    return { cancelled, foreground, foregroundMilliseconds: performance.now() - started };
  });
  writeFileSync(testInfo.outputPath("cancelled-bulk-capacity.json"), JSON.stringify(result, null, 2));
  await testInfo.attach("cancelled-bulk-capacity", { body: JSON.stringify(result, null, 2), contentType: "application/json" });
  expect(result.cancelled).toBe("AbortError");
  expect(result.foreground).toEqual([]);
  expect(result.foregroundMilliseconds).toBeLessThan(500);
});

test("issue190_native_bulk_abort_releases_capacity_preserves_retained_indexes_and_restarts", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.addScriptTag({ content: await nativeHarness() });
  const observations = process.env.TEMPO_RESTORATION_QUALIFICATION === "1" ? 1000 : 1;
  test.setTimeout(observations === 1000 ? 180_000 : 60_000);
  const workloadPath = process.env.TEMPO_RESTORATION_WORKLOAD;
  if (observations === 1000 && !workloadPath) throw new Error("Qualification requires the generated diverse public workload");
  const publicPayload = workloadPath ? JSON.parse(await readFile(workloadPath, "utf8")) : undefined;
  const result = await page.evaluate(async ({ observations, publicPayload }) => {
    const native = Reflect.get(globalThis, "tempoNative") as {
      runStudyTask: (task: unknown, signal?: AbortSignal) => Promise<unknown>;
      browserActivitySnapshot: () => { id: string; state: string }[];
    };
    const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    const retainedLine = { id: "retained", repertoireId: "retained", repertoireName: "Retained", title: "Retained", side: "white", startingFen: startFen, moves: ["e2e4", "e7e5"], validation: { isValid: true, isTruncated: false, diagnostics: [] } };
    await native.runStudyTask({ kind: "initializePositionIndex", repertoireId: "retained", revision: 1, lines: [retainedLine] });
    const before = await native.runStudyTask({ kind: "findPositionMatches", repertoireId: "retained", revision: 1, fen: startFen });
    await native.runStudyTask({ kind: "initializeComparisonIndex", revision: 1, cards: [{ id: "comparison-retained", start_fen: startFen, moves: ["e2e4", "e7e5"], state: "new", kind: "opening", trained_color: "white", repertoires: [{ id: "retained", name: "Retained" }] }] });
    const comparisonBefore = await native.runStudyTask({ kind: "findComparisonMatches", revision: 1, fen: startFen, repertoireId: "retained" });
    const lines = publicPayload?.lines ?? Array.from({ length: 8268 }, (_, index) => ({ id: `public-${index}`, repertoire_id: `public-${index % 10}`,
      repertoire_name: "Public synthetic", name: `Line ${index}`, trained_color: "white", start_fen: startFen,
      moves: Array.from({ length: 8 }, () => ["g1f3", "g8f6", "f3g1", "f6g8"]).flat() }));
    const awaitRunning = async () => {
      const deadline = performance.now() + 10_000;
      while (!native.browserActivitySnapshot().some(item => item.id.startsWith("repertoire:") && item.state === "running")) {
        if (performance.now() > deadline) throw new Error("Native bulk worker never reported running");
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      }
    };
    const foregroundCards = Array.from({ length: 20 }, (_, index) => ({ id: `foreground-${index}`, queue_entry_id: index + 100,
      first_correct_at: null, start_fen: startFen, moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
      repertoire_name: "Public foreground", repertoire_source: "synthetic", trained_color: "white", revision: 1 }));
    const foregroundRepertoires = Array.from({ length: 10 }, (_, index) => ({ id: `foreground-${index}`,
      name: "Public foreground", source_name: "synthetic", created_at: "2026-09-18", line_count: 826, card_count: 100, due_count: 20 }));
    const foregroundPuzzles = Array.from({ length: 8 }, (_, index) => ({ DeckId: "public-tactics", DeckPosition: index + 1,
      PuzzleId: `public-${index}`, FEN: startFen, Moves: "e2e4 e7e5 g1f3", Rating: 1500 }));
    const tasks = {
      queue: { kind: "queue", payload: { cards: foregroundCards, count: foregroundCards.length } },
      workspace: { kind: "workspace", url: "/api/repertoires", payload: { repertoires: foregroundRepertoires } },
      tactics: { kind: "deck", records: foregroundPuzzles, deckId: "public-tactics" },
      route: { kind: "resolveTrainingRoute", repertoireId: "retained", startingFen: startFen, moves: ["e2e4"], lines: [retainedLine] },
    };
    const families = ["queue", "workspace", "tactics", "route"] as const;
    const foregroundOutputs = await Promise.all(families.map(family => native.runStudyTask(tasks[family])));
    const foregroundCounts = foregroundOutputs.map((output, index) => index === 1
      ? (output as { repertoires: unknown[] }).repertoires.length : (output as unknown[]).length);
    if (JSON.stringify(foregroundCounts) !== "[20,10,8,1]") throw new Error(`Foreground workload rejected records: ${JSON.stringify(foregroundCounts)}`);
    const sampleForeground = async (kind: typeof families[number]) => {
      const started = performance.now();
      await native.runStudyTask(tasks[kind]);
      return { kind, milliseconds: performance.now() - started };
    };
    for (let warmup = 0; warmup < 10; warmup++) for (const family of families) await sampleForeground(family);
    const baseline = [];
    for (let observation = 0; observation < observations; observation++) for (const family of families) baseline.push(await sampleForeground(family));
    const measurements = [];
    for (let generation = 1; generation <= 3; generation++) {
      const cancellation = new AbortController();
      const work = native.runStudyTask({ kind: "repertoirePreparation", generation, payload: { lines } }, cancellation.signal);
      const settled = work.catch(error => error.name);
      await awaitRunning();
      for (let observation = 0; observation < observations; observation++) for (const family of families) measurements.push(await sampleForeground(family));
      const queue = await native.runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } });
      cancellation.abort();
      if (await settled !== "AbortError") throw new Error("Obsolete bulk work was not cancelled");
      if (JSON.stringify(queue) !== "[]") throw new Error("Foreground queue output changed");
    }
    const after = await native.runStudyTask({ kind: "findPositionMatches", repertoireId: "retained", revision: 1, fen: startFen });
    const comparisonAfter = await native.runStudyTask({ kind: "findComparisonMatches", revision: 1, fen: startFen, repertoireId: "retained" });
    const recovered = await native.runStudyTask({ kind: "repertoirePreparation", generation: 4, payload: { lines: lines.slice(0, 1) } }) as { generation: number; positions: unknown[] };
    return { baseline, measurements, parity: JSON.stringify(before) === JSON.stringify(after), comparisonParity: JSON.stringify(comparisonBefore) === JSON.stringify(comparisonAfter), comparisonCount: (comparisonBefore as unknown[]).length, generation: recovered.generation, positionCount: recovered.positions.length };
  }, { observations, publicPayload });
  writeFileSync(testInfo.outputPath("native-worker-results.json"), JSON.stringify(result, null, 2));
  expect(result.parity).toBe(true);
  expect(result.comparisonParity).toBe(true);
  expect(result.comparisonCount).toBeGreaterThan(0);
  expect(result.generation).toBe(4);
  expect(result.positionCount).toBeGreaterThan(0);
  for (const family of ["queue", "workspace", "tactics", "route"]) {
    const baseline = result.baseline.filter(row => row.kind === family).map(row => row.milliseconds);
    const loaded = result.measurements.filter(row => row.kind === family).map(row => row.milliseconds);
    const paired = loaded.map((value, index) => value - baseline[index % baseline.length]).toSorted((left, right) => left - right);
    expect(paired[Math.ceil(paired.length * .95) - 1]).toBeLessThan(50);
  }
  expect(Math.max(...result.measurements.map(row => row.milliseconds))).toBeLessThan(500);
  await testInfo.attach("native-worker-results", { body: JSON.stringify(result, null, 2), contentType: "application/json" });
});
