import { expect, test } from "./observability";
import { build } from "esbuild";
import { readFile } from "node:fs/promises";

// Only the worker asset URL is rebound to a Blob. Dispatcher, computation,
// position store, validation and cancellation are the actual product modules.
async function nativeHarness() {
  const worker = await build({ entryPoints: ["app/lib/study.worker.ts"], bundle: true, write: false, format: "iife" });
  const dispatcher = await build({ stdin: { contents: `import { runStudyTask } from './app/lib/background-study';
    import { browserActivitySnapshot } from './app/lib/browser-activity';
    globalThis.tempoNative = { runStudyTask, browserActivitySnapshot };`, resolveDir: process.cwd() },
    bundle: true, write: false, format: "iife", plugins: [{ name: "native-worker-asset", setup(builder) {
      builder.onLoad({ filter: /(?:background-study|repertoire-preparation)\.ts$/ }, async args => ({
        contents: (await readFile(args.path, "utf8")).replaceAll('new URL("./study.worker.ts", import.meta.url)', 'globalThis.tempoNativeWorkerUrl'), loader: "ts",
      }));
    } }] });
  return `globalThis.tempoNativeWorkerUrl = URL.createObjectURL(new Blob([${JSON.stringify(worker.outputFiles[0].text)}], {type:'text/javascript'}));\n${dispatcher.outputFiles[0].text}`;
}

test("issue190_native_bulk_abort_releases_capacity_preserves_retained_indexes_and_restarts", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.addScriptTag({ content: await nativeHarness() });
  const observations = process.env.TEMPO_RESTORATION_QUALIFICATION === "1" ? 1000 : 1;
  const result = await page.evaluate(async observations => {
    const native = Reflect.get(globalThis, "tempoNative") as {
      runStudyTask: (task: unknown, signal?: AbortSignal) => Promise<unknown>;
      browserActivitySnapshot: () => { id: string; state: string }[];
    };
    const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    const retainedLine = { id: "retained", repertoireId: "retained", repertoireName: "Retained", title: "Retained", side: "white", startingFen: startFen, moves: ["e2e4", "e7e5"], validation: { isValid: true, isTruncated: false, diagnostics: [] } };
    await native.runStudyTask({ kind: "initializePositionIndex", repertoireId: "retained", revision: 1, lines: [retainedLine] });
    const before = await native.runStudyTask({ kind: "findPositionMatches", repertoireId: "retained", revision: 1, fen: startFen });
    const lines = Array.from({ length: 8268 }, (_, index) => ({ id: `public-${index}`, repertoire_id: `public-${index % 10}`,
      repertoire_name: "Public synthetic", name: `Line ${index}`, trained_color: "white", start_fen: startFen,
      moves: Array.from({ length: 8 }, () => ["g1f3", "g8f6", "f3g1", "f6g8"]).flat() }));
    const awaitRunning = async () => {
      const deadline = performance.now() + 10_000;
      while (!native.browserActivitySnapshot().some(item => item.id.startsWith("repertoire:") && item.state === "running")) {
        if (performance.now() > deadline) throw new Error("Native bulk worker never reported running");
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      }
    };
    const sampleForeground = async () => {
      const started = performance.now();
      await native.runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } });
      return performance.now() - started;
    };
    for (let warmup = 0; warmup < 10; warmup++) await sampleForeground();
    const baseline = [];
    for (let observation = 0; observation < observations; observation++) baseline.push(await sampleForeground());
    const measurements = [];
    for (let generation = 1; generation <= 3; generation++) {
      const cancellation = new AbortController();
      const work = native.runStudyTask({ kind: "repertoirePreparation", generation, payload: { lines } }, cancellation.signal);
      const settled = work.catch(error => error.name);
      await awaitRunning();
      for (let observation = 0; observation < observations; observation++) measurements.push(await sampleForeground());
      const queue = await native.runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } });
      cancellation.abort();
      if (await settled !== "AbortError") throw new Error("Obsolete bulk work was not cancelled");
      if (JSON.stringify(queue) !== "[]") throw new Error("Foreground queue output changed");
    }
    const after = await native.runStudyTask({ kind: "findPositionMatches", repertoireId: "retained", revision: 1, fen: startFen });
    const recovered = await native.runStudyTask({ kind: "repertoirePreparation", generation: 4, payload: { lines: lines.slice(0, 1) } }) as { generation: number; positions: unknown[] };
    return { baseline, measurements, parity: JSON.stringify(before) === JSON.stringify(after), generation: recovered.generation, positionCount: recovered.positions.length };
  }, observations);
  expect(result.parity).toBe(true);
  expect(result.generation).toBe(4);
  expect(result.positionCount).toBeGreaterThan(0);
  expect(Math.max(...result.measurements)).toBeLessThan(500);
  await testInfo.attach("native-worker-results", { body: JSON.stringify(result, null, 2), contentType: "application/json" });
});
