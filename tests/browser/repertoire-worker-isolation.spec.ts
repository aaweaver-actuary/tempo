import { expect, test as base } from "./observability";
import { api } from "./product-fixtures";
import { assertDisposableTarget } from "./disposable-target";
import { build } from "esbuild";
import { readFile } from "node:fs/promises";
import { writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { relative } from "node:path";

const test = base.extend<{ nativeMeasurementControls: void }>({
  nativeMeasurementControls: [async ({ request }, runMeasurement) => {
    assertDisposableTarget(await (await request.get(`${api}/health`)).json());
    const original = await (await request.get(`${api}/settings`)).json();
    expect((await request.put(`${api}/settings`, { data: { ...original, study_only_mode: true,
      optional_analysis_and_imports_enabled: false } })).ok()).toBe(true);
    // Only the explicit dispatcher workload runs in this native capacity probe.
    // Full UI qualification separately exercises actual restoration controls.
    try { await runMeasurement(); }
    finally { expect((await request.put(`${api}/settings`, { data: original })).ok()).toBe(true); }
  }, { auto: true }],
});

// Only the worker asset URL is rebound to a Blob. Dispatcher, computation,
// position store, validation and cancellation are the actual product modules.
async function nativeHarness(barrierPhase?: string) {
  const sourcePlugin = { name: "native-worker-source", setup(builder: import("esbuild").PluginBuild) {
    builder.onLoad({ filter: /\/app\/.*\.tsx?$/ }, async args => {
      let contents = process.env.TEMPO_ISSUE190_SHARED_BASELINE === "1"
        ? execFileSync("git", ["show", `3f7332399796366eb32f9fcb5e1b5c678a5b1562:${relative(process.cwd(), args.path)}`], { encoding: "utf8" })
        : await readFile(args.path, "utf8");
      if (barrierPhase) {
        const checkpoints: Record<string, [string, string]> = {
          "/validated-data.ts": ["return records.flatMap((raw) => {", "validation"],
          "/canonical-line.ts": ["moves.push(asUciMove(`${played.from}${played.to}${played.promotion ?? \"\"}`));", "adaptation"],
          "/position-similarity.ts": ["for (const [moveIndex, uci] of line.moves.entries()) {", "index"],
          "/repertoire-alternative-index.ts": ["for (const position of positions) {", "snapshot"],
          "/study.worker.ts": ["const result = runStudyTaskInWorker(data.task);", "publication"],
        };
        for (const [suffix, [anchor, phase]] of Object.entries(checkpoints)) {
          if (!args.path.endsWith(suffix)) continue;
          if (!contents.includes(anchor)) throw new Error(`Native phase checkpoint missing: ${suffix}`);
          contents = contents.replace(anchor, `${anchor} Reflect.get(globalThis, "tempoPhaseCheckpoint")?.(${JSON.stringify(phase)});`);
        }
        if (args.path.endsWith("/study.worker.ts")) {
          contents = contents.replace("clearDataDiagnostics();", `clearDataDiagnostics();
            if (${JSON.stringify(barrierPhase)} === "crash" && data.task.kind === "repertoirePreparation" && data.task.generation === 1)
              throw new Error("Deliberate native worker crash");
            Reflect.set(globalThis, "tempoPhaseCheckpoint", (phase: string) => {
              if (data.task.kind !== "repertoirePreparation" || data.task.generation !== 1 || phase !== ${JSON.stringify(barrierPhase)}) return;
              self.postMessage({ tempoTestPhase: phase });
              // Deliberately hold the real worker at a pipeline checkpoint.
              // Only native Worker.terminate can release this computation.
              for (;;) { /* phase barrier */ }
            });`);
        }
      }
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
  await page.exposeFunction("tempoPersistNativeProgress", (progress: { phase: string; baseline: unknown[]; measurements: unknown[] }) => {
    writeFileSync(testInfo.outputPath("native-worker-progress.json"), JSON.stringify(progress, null, 2));
    console.log(`Native ${testInfo.project.name}: ${progress.phase}, ${progress.baseline.length} baseline / ${progress.measurements.length} loaded requests`);
  });
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
      name: "Public foreground", source_name: "synthetic", created_at: "2026-09-18T00:00:00Z", line_count: 826, card_count: 100, due_count: 20 }));
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
    const baseline: Awaited<ReturnType<typeof sampleForeground>>[] = [];
    const measurements: Awaited<ReturnType<typeof sampleForeground>>[] = [];
    const persistProgress = async (phase: string) => Reflect.get(globalThis, "tempoPersistNativeProgress")({ phase, baseline, measurements });
    for (let observation = 0; observation < observations; observation++) {
      for (const family of families) baseline.push(await sampleForeground(family));
      if ((observation + 1) % 100 === 0) await persistProgress("baseline");
    }
    for (let generation = 1; generation <= 3; generation++) {
      const cancellation = new AbortController();
      const work = native.runStudyTask({ kind: "repertoirePreparation", generation, payload: { lines } }, cancellation.signal);
      const settled = work.catch(error => error.name);
      await awaitRunning();
      for (let observation = 0; observation < observations; observation++) {
        for (const family of families) measurements.push(await sampleForeground(family));
        if ((observation + 1) % 100 === 0) await persistProgress(`generation ${generation}`);
      }
      const queue = await native.runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } });
      cancellation.abort();
      if (await settled !== "AbortError") throw new Error("Obsolete bulk work was not cancelled");
      if (JSON.stringify(queue) !== "[]") throw new Error("Foreground queue output changed");
    }
    const after = await native.runStudyTask({ kind: "findPositionMatches", repertoireId: "retained", revision: 1, fen: startFen });
    await persistProgress("index retention and recovery");
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

for (const phase of ["validation", "adaptation", "index", "snapshot", "publication"])
test(`restoration_native_cancel_during_${phase}_terminates_computation_and_preserves_foreground`, async ({ page }, testInfo) => {
  await page.goto("/");
  await page.evaluate(() => {
    const NativeWorker = window.Worker;
    const probe = { phase: "", bulkCreated: 0, bulkTerminated: 0, foregroundTerminated: 0 };
    Reflect.set(window, "tempoPhaseProbe", probe);
    window.Worker = class extends NativeWorker {
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options);
        if (options?.name === "tempo-repertoire-preparation") probe.bulkCreated++;
        // Observe the phase before the product's protocol listener. No product
        // result, error, ownership or termination behavior is replaced.
        this.addEventListener("message", event => {
          if (event.data.tempoTestPhase) { probe.phase = event.data.tempoTestPhase; event.stopImmediatePropagation(); }
        });
        const terminate = this.terminate.bind(this);
        this.terminate = () => {
          if (options?.name === "tempo-repertoire-preparation") probe.bulkTerminated++;
          else probe.foregroundTerminated++;
          terminate();
        };
      }
    };
  });
  await page.addScriptTag({ content: await nativeHarness(phase) });
  await page.evaluate(async () => {
    const native = Reflect.get(window, "tempoNative");
    const fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    const line = { id: "retained", repertoireId: "retained", repertoireName: "Retained", title: "Retained",
      side: "white", startingFen: fen, moves: ["e2e4", "e7e5"], validation: { isValid: true, isTruncated: false, diagnostics: [] } };
    await native.runStudyTask({ kind: "initializePositionIndex", repertoireId: "retained", revision: 1, lines: [line] });
    const before = await native.runStudyTask({ kind: "findPositionMatches", repertoireId: "retained", revision: 1, fen });
    const payload = { lines: [{ id: "bulk", repertoire_id: "bulk", repertoire_name: "Bulk", name: "Bulk",
      trained_color: "white", start_fen: fen, moves: ["e2e4", "e7e5", "g1f3"] }] };
    const controller = new AbortController();
    const pending = native.runStudyTask({ kind: "repertoirePreparation", generation: 1, payload }, controller.signal)
      .catch((error: Error) => error.name);
    Reflect.set(window, "tempoPhaseRun", { before, fen, payload, controller, pending });
  });
  await expect.poll(() => page.evaluate(() => Reflect.get(window, "tempoPhaseProbe").phase)).toBe(phase);
  const evidence = await page.evaluate(async () => {
    const native = Reflect.get(window, "tempoNative");
    const run = Reflect.get(window, "tempoPhaseRun");
    run.controller.abort();
    const cancelled = await run.pending;
    const after = await native.runStudyTask({ kind: "findPositionMatches", repertoireId: "retained", revision: 1, fen: run.fen });
    const retry = await native.runStudyTask({ kind: "repertoirePreparation", generation: 2, payload: run.payload });
    return { cancelled, before: run.before, after, retryGeneration: retry.generation, probe: Reflect.get(window, "tempoPhaseProbe") };
  });
  expect(evidence.cancelled).toBe("AbortError");
  expect(evidence.after).toEqual(evidence.before);
  expect(evidence.before.length).toBeGreaterThan(0);
  expect(evidence.retryGeneration).toBe(2);
  expect(evidence.probe).toMatchObject({ bulkCreated: 2, bulkTerminated: 2, foregroundTerminated: 0 });
  await testInfo.attach("native-phase-cancellation", { body: JSON.stringify(evidence), contentType: "application/json" });
});

// Real same-origin storage delivery and real PostgreSQL settings receipts.
// The held read creates the ordering race; the hook and save path are product code.
test("restoration_native_two_tab_confirmed_disable_rejects_older_settings_read", async ({ page, context, request }, testInfo) => {
  const original = await (await request.get(`${api}/settings`)).json();
  const enabled = { ...original, study_only_mode: false, repertoire_preparation_enabled: true, optional_analysis_and_imports_enabled: false };
  expect((await request.put(`${api}/settings`, { data: enabled })).ok()).toBe(true);
  const controlsBundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import { createRoot } from 'react-dom/client';
    import { createElement } from 'react';
    import { useStudyControls } from './app/hooks/use-study-only-mode';
    import { saveLocalSettings } from './app/lib/settings-save';
    import { announceConfirmedStudyControlsChange } from './app/lib/study-controls-change';
    function Probe() { const controls = useStudyControls(); return createElement('output', {id:'native-controls'},
      controls === null ? 'unknown' : String(!controls.studyOnlyMode && controls.repertoirePreparationEnabled)); }
    const root = document.createElement('div'); document.body.append(root); createRoot(root).render(createElement(Probe));
    globalThis.tempoSaveControls = async (settings) => { await saveLocalSettings(settings); announceConfirmedStudyControlsChange(); };
  ` }, define: { __TEMPO_API_URL__: JSON.stringify(api.replace(/\/api$/, "")) }, bundle: true, write: false, format: "iife" });
  const second = await context.newPage();
  let releaseRead!: () => void;
  let heldRead = false;
  let holdNext = false;
  let failReads = false;
  await page.route("**/api/settings", async route => {
    if (route.request().method() !== "GET") return route.continue();
    if (failReads) return route.fulfill({ status: 503, body: "Settings unavailable" });
    if (holdNext) {
      holdNext = false;
      heldRead = true;
      await new Promise<void>(resolve => { releaseRead = resolve; });
      return route.fulfill({ json: enabled });
    }
    return route.continue();
  });
  try {
    await page.goto("/");
    await page.addScriptTag({ content: controlsBundle.outputFiles[0].text });
    await expect(page.locator("#native-controls")).toHaveText("true");
    holdNext = true;
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await expect.poll(() => heldRead).toBe(true);
    await second.goto("/");
    await second.addScriptTag({ content: controlsBundle.outputFiles[0].text });
    await second.evaluate(async settings => { await Reflect.get(window, "tempoSaveControls")(settings); },
      { ...enabled, repertoire_preparation_enabled: false });
    // The successful database save invalidates the other tab before its GET ends.
    await expect(page.locator("#native-controls")).toHaveText("false");
    failReads = true;
    releaseRead();
    await expect(page.locator("#native-controls")).toHaveText("false");
    const persisted = await (await request.get(`${api}/settings`)).json();
    expect(persisted.repertoire_preparation_enabled).toBe(false);
    await testInfo.attach("two-tab-confirmed-disable", { body: JSON.stringify({ persistedPreparationEnabled: persisted.repertoire_preparation_enabled, staleReadReleased: true }), contentType: "application/json" });
  } finally {
    releaseRead?.();
    await second.close();
    expect((await request.put(`${api}/settings`, { data: original })).ok()).toBe(true);
  }
});

test("restoration_native_repeated_cancel_replacement_and_crash_retry_release_owned_workers", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.evaluate(() => {
    const NativeWorker = window.Worker;
    const probe = { bulkCreated: 0, bulkTerminated: 0, phaseReady: undefined as (() => void) | undefined };
    Reflect.set(window, "tempoRepeatProbe", probe);
    window.Worker = class extends NativeWorker {
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options);
        if (options?.name === "tempo-repertoire-preparation") probe.bulkCreated++;
        this.addEventListener("message", event => {
          if (event.data.tempoTestPhase) { event.stopImmediatePropagation(); probe.phaseReady?.(); }
        });
        const terminate = this.terminate.bind(this);
        this.terminate = () => { if (options?.name === "tempo-repertoire-preparation") probe.bulkTerminated++; terminate(); };
      }
    };
  });
  await page.addScriptTag({ content: await nativeHarness("index") });
  const repetitions = await page.evaluate(async () => {
    const native = Reflect.get(window, "tempoNative");
    const probe = Reflect.get(window, "tempoRepeatProbe");
    const payload = { lines: [{ id: "bulk", repertoire_id: "bulk", repertoire_name: "Bulk", name: "Bulk",
      trained_color: "white", start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves: ["e2e4", "e7e5"] }] };
    Reflect.set(window, "tempoRepeatedPayload", payload);
    const outcomes: string[] = [];
    for (let index = 0; index < 12; index++) {
      const ready = new Promise<void>(resolve => { probe.phaseReady = resolve; });
      const controller = new AbortController();
      const first = native.runStudyTask({ kind: "repertoirePreparation", generation: 1, payload }, controller.signal)
        .catch((error: Error) => error.name);
      await ready;
      if (index % 2 === 0) controller.abort();
      // B replaces A in alternate iterations and must finish independently.
      const newer = await native.runStudyTask({ kind: "repertoirePreparation", generation: 2, payload });
      if (newer.generation !== 2) throw new Error("New request did not publish independently");
      outcomes.push(await first);
    }
    return { outcomes, created: probe.bulkCreated, terminated: probe.bulkTerminated,
      active: native.browserActivitySnapshot().filter((item: { id: string; state: string }) => item.id.startsWith("repertoire:") && ["queued", "running"].includes(item.state)).length };
  });
  expect(repetitions.outcomes).toEqual(Array(12).fill("AbortError"));
  expect(repetitions).toMatchObject({ created: 24, terminated: 24, active: 0 });
  await page.addScriptTag({ content: await nativeHarness("crash") });
  const recovery = await page.evaluate(async () => {
    const native = Reflect.get(window, "tempoNative");
    const payload = Reflect.get(window, "tempoRepeatedPayload");
    const error = await native.runStudyTask({ kind: "repertoirePreparation", generation: 1, payload }).catch((failure: Error) => failure.message);
    const retry = await native.runStudyTask({ kind: "repertoirePreparation", generation: 2, payload });
    const probe = Reflect.get(window, "tempoRepeatProbe");
    return { error, generation: retry.generation, created: probe.bulkCreated, terminated: probe.bulkTerminated };
  });
  expect(recovery.error).toContain("Retry loading lines");
  expect(recovery).toMatchObject({ generation: 2, created: 26, terminated: 26 });
  await testInfo.attach("native-repetition-and-recovery", { body: JSON.stringify({ repetitions, recovery }), contentType: "application/json" });
});
