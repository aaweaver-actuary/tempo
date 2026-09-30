import { test, expect } from "./observability";
import { Chess } from "chess.js";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { navigate } from "./ui-fixtures";
import { prepareVisualUI, type VisualRepertoireLine } from "./visual-fixtures";

const performanceOutputDirectory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
function writePerformanceReport(fileName: string, reportBody: string) {
  mkdirSync(performanceOutputDirectory, { recursive: true });
  writeFileSync(join(performanceOutputDirectory, fileName), `${reportBody}\n`);
}

function summarize(samples: number[]) {
  const sorted = samples.toSorted((left, right) => left - right);
  return {
    p50: sorted[Math.ceil(sorted.length * 0.5) - 1],
    p95: sorted[Math.ceil(sorted.length * 0.95) - 1],
  };
}

test("warm workspace and Builder move responsiveness", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await prepareVisualUI(page, false);
  const boardReadySamples: number[] = [];
  for (let reload = 0; reload < 5; reload++) {
    if (reload > 0) await page.reload();
    await navigate(page, "Builder");
    await expect.poll(() => page.evaluate(() =>
      performance.getEntriesByName("tempo:board-ready").at(-1)?.duration ?? null,
    )).not.toBeNull();
    boardReadySamples.push(await page.evaluate(() =>
      performance.getEntriesByName("tempo:board-ready").at(-1)!.duration,
    ));
  }
  const modes = ["Builder", "Games", "Endgames", "Tactics", "Train"] as const;
  for (const mode of modes) await navigate(page, mode);
  const viewSwitchSamples: Record<string, number[]> = Object.fromEntries(
    modes.map((mode) => [mode, []]),
  );
  for (let iteration = 0; iteration < 5; iteration++) {
    for (const mode of modes) {
      await navigate(page, mode);
      await page.evaluate(() => new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ));
      viewSwitchSamples[mode].push(await page.evaluate(() =>
        performance.getEntriesByName("tempo:view-switch").at(-1)!.duration,
      ));
    }
  }
  await navigate(page, "Builder");
  const separator = page.getByRole("separator", { name: "Board size" });
  await separator.focus();
  await page.evaluate(() => new Promise<void>((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
  ));
  await page.evaluate(() => {
    const entries: number[] = [];
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) entries.push(entry.duration);
    });
    observer.observe({ entryTypes: ["longtask"] });
    Object.assign(window, { tempoInteractionTasks: entries });
    type EventSample = {
      name: string; inputDelay: number; handlerDuration: number;
      presentationDelay: number; duration: number;
    };
    const eventSamples: EventSample[] = [];
    const collectEvents = (observedEntries: PerformanceEntryList) => {
      for (const entry of observedEntries) {
        const event = entry as PerformanceEntry & {
          processingStart: number; processingEnd: number;
        };
        if (!["click", "keydown", "pointerup"].includes(event.name)) continue;
        eventSamples.push({
          name: event.name,
          inputDelay: event.processingStart - event.startTime,
          handlerDuration: event.processingEnd - event.processingStart,
          presentationDelay: Math.max(0, event.duration - (event.processingEnd - event.startTime)),
          duration: event.duration,
        });
      }
    };
    let eventObserver: PerformanceObserver | null = null;
    if (PerformanceObserver.supportedEntryTypes.includes("event")) {
      eventObserver = new PerformanceObserver((list) => collectEvents(list.getEntries()));
      eventObserver.observe({ type: "event", durationThreshold: 16 } as PerformanceObserverInit);
    }
    Object.assign(window, { tempoEventTiming: { supported: eventObserver !== null, samples: eventSamples, observer: eventObserver, collectEvents } });
  });
  for (let index = 0; index < 6; index++)
    await page.keyboard.press(index % 2 ? "ArrowLeft" : "ArrowRight");
  const boardBounds = (await page.locator(".cg-wrap").boundingBox())!;
  const moveToPaintSamples: number[] = [];
  for (let repetition = 0; repetition < 5; repetition++) {
    for (const rank of [6, 4])
      await page.mouse.click(
        boardBounds.x + (4.5 * boardBounds.width) / 8,
        boardBounds.y + ((rank + 0.5) * boardBounds.height) / 8,
      );
    await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3/);
    await page.evaluate(() => new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    ));
    const moveDuration = await page.evaluate(() =>
      performance.getEntriesByName("tempo:move-to-paint").at(-1)?.duration ?? null,
    );
    expect(moveDuration).not.toBeNull();
    moveToPaintSamples.push(moveDuration!);
    if (repetition < 4) {
      await page.locator(".shared-board-toolbar .board-tools")
        .getByRole("button", { name: /Back/ }).click();
      await expect(page.locator(".board-frame")).not.toHaveAttribute("data-fen", /4P3/);
    }
  }
  const longTasks = await page.evaluate(() =>
    Reflect.get(window, "tempoInteractionTasks") as number[],
  );
  const eventTiming = await page.evaluate(() => {
    const captured = Reflect.get(window, "tempoEventTiming") as {
      supported: boolean;
      samples: Array<{ name: string; inputDelay: number; handlerDuration: number; presentationDelay: number; duration: number }>;
      observer: PerformanceObserver | null;
      collectEvents: (entries: PerformanceEntryList) => void;
    };
    if (captured.observer) {
      captured.collectEvents(captured.observer.takeRecords());
      captured.observer.disconnect();
    }
    return { supported: captured.supported, samples: captured.samples };
  });
  const browserName = testInfo.project.use.browserName ?? "chromium";
  const report = {
    schemaVersion: 1,
    timestamp: new Date().toISOString(),
    commit: process.env.TEMPO_COMMIT ?? process.env.GITHUB_SHA ?? null,
    fixture: { name: "prepareVisualUI-default", boardReloads: 5, warmMoves: 5 },
    browser: browserName,
    viewSwitchSamples,
    viewSwitchSummary: Object.fromEntries(
      Object.entries(viewSwitchSamples).map(([mode, samples]) => [mode, summarize(samples)]),
    ),
    boardReadySamples,
    boardReadySummary: summarize(boardReadySamples),
    moveToPaintSamples,
    moveToPaintSummary: summarize(moveToPaintSamples),
    longTasks,
    eventTiming,
  };
  const reportBody = JSON.stringify(report, null, 2);
  writePerformanceReport(`browser-${browserName}.json`, reportBody);
  await testInfo.attach("interaction-performance", {
    body: reportBody,
    contentType: "application/json",
  });
  expect(boardReadySamples).toHaveLength(5);
  expect(moveToPaintSamples).toHaveLength(5);
  if (browserName === "chromium") {
    expect(eventTiming.supported).toBe(true);
    expect(eventTiming.samples.length).toBeGreaterThan(0);
  }
  for (const samples of Object.values(viewSwitchSamples))
    expect(summarize(samples).p95).toBeLessThanOrEqual(300);
  expect(longTasks.filter((duration) => duration > 100)).toEqual([]);
  for (const sample of eventTiming.samples) {
    expect(sample.inputDelay).toBeGreaterThanOrEqual(0);
    expect(sample.handlerDuration).toBeGreaterThanOrEqual(0);
    expect(sample.presentationDelay).toBeGreaterThanOrEqual(0);
  }
});

test("warm training cards advance to the next visible paint", async ({ page }, testInfo) => {
  const trainingCards = ["e2e4", "d2d4", "g1f3", "c2c4", "b1c3", "f2f4"]
    .map((move, index) => ({
      id: `performance-card-${index + 1}`,
      queue_entry_id: index + 1,
      start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
      moves: [move],
      content_type: "opening" as const,
      repertoire_name: `Transition card ${index + 1}`,
      repertoire_source: "PGN",
      first_correct_at: "2026-09-17T12:00:00Z",
      trained_color: "white" as const,
    }));
  await page.setViewportSize({ width: 1280, height: 800 });
  await prepareVisualUI(page, false, trainingCards);
  await navigate(page, "Train");
  await expect(page.locator(".opening-title h2")).toHaveText("Transition card 1");
  await page.evaluate(() => {
    const samples: Array<{ from: string; to: string; clickToPaintMs: number }> = [];
    Object.assign(window, { tempoTrainingCardSamples: samples });
    document.addEventListener("click", (event) => {
      const clickedButton = (event.target as Element).closest("button");
      if (!clickedButton?.textContent?.includes("Correct")) return;
      const expectedTitle = Reflect.get(window, "tempoExpectedNextCard") as string | undefined;
      const previousTitle = document.querySelector(".opening-title h2")?.textContent?.trim();
      if (!expectedTitle || !previousTitle) return;
      const clickedAt = performance.now();
      const observer = new MutationObserver(() => {
        if (document.querySelector(".opening-title h2")?.textContent?.trim() !== expectedTitle) return;
        observer.disconnect();
        requestAnimationFrame(() => requestAnimationFrame(() => {
          samples.push({ from: previousTitle, to: expectedTitle, clickToPaintMs: performance.now() - clickedAt });
        }));
      });
      observer.observe(document.body, { subtree: true, childList: true, characterData: true });
    }, true);
  });
  for (let index = 1; index < trainingCards.length; index++) {
    const nextTitle = trainingCards[index].repertoire_name;
    await page.evaluate((title) => Object.assign(window, { tempoExpectedNextCard: title }), nextTitle);
    await page.getByRole("button", { name: "Correct", exact: true }).click();
    await expect(page.locator(".opening-title h2")).toHaveText(nextTitle);
    await expect.poll(() => page.evaluate(() =>
      (Reflect.get(window, "tempoTrainingCardSamples") as unknown[]).length,
    )).toBe(index);
  }
  const samples = await page.evaluate(() => Reflect.get(window, "tempoTrainingCardSamples") as Array<{
    from: string; to: string; clickToPaintMs: number;
  }>);
  const report = {
    schemaVersion: 1,
    timestamp: new Date().toISOString(),
    commit: process.env.TEMPO_COMMIT ?? process.env.GITHUB_SHA ?? null,
    fixture: { name: "six prepared single-move opening cards", cards: trainingCards.length },
    samples,
    summary: summarize(samples.map((sample) => sample.clickToPaintMs)),
  };
  const reportBody = JSON.stringify(report, null, 2);
  writePerformanceReport("training-card-chromium.json", reportBody);
  await testInfo.attach("training-card-performance", {
    body: reportBody,
    contentType: "application/json",
  });
  expect(samples).toHaveLength(5);
  expect(report.summary.p95).toBeLessThanOrEqual(500);
});

test("Builder similarity worker messages keep the position index in the worker", async ({ page }, testInfo) => {
  const startingFen = new Chess().fen();
  const openingPairs = new Chess().moves({ verbose: true }).flatMap((firstMove) => {
    const board = new Chess();
    board.move(firstMove);
    return board.moves({ verbose: true }).map((reply) => [
      `${firstMove.from}${firstMove.to}${firstMove.promotion ?? ""}`,
      `${reply.from}${reply.to}${reply.promotion ?? ""}`,
    ]);
  });
  const repertoireLines: VisualRepertoireLine[] = Array.from({ length: 250 }, (_, index) => ({
    id: `performance-line-${index}`,
    repertoire_id: "visual-repertoire",
    repertoire_name: "Spanish opening",
    name: `Opening pair ${index}`,
    trained_color: "white",
    start_fen: startingFen,
    moves: openingPairs[index % openingPairs.length],
  }));
  await page.addInitScript(() => {
    type ObservedMessage = {
      kind: string; bytes: number; hasPositions: boolean; startedAt: number; fen?: string;
      queueMs?: number; computeMs?: number; roundtripMs?: number; paintMs?: number;
    };
    const observed: ObservedMessage[] = [];
    Object.assign(window, { tempoStudyMessages: observed });
    const originalPostMessage = Worker.prototype.postMessage;
    const observedWorkers = new WeakMap<Worker, Map<number, ObservedMessage>>();
    Object.defineProperty(Worker.prototype, "postMessage", {
      configurable: true,
      value: function(this: Worker, message: unknown, ...options: unknown[]) {
        const task = typeof message === "object" && message !== null
          ? Reflect.get(message, "task") : undefined;
        if (task && typeof task === "object") {
          const kind = Reflect.get(task, "kind");
          if (kind === "initializePositionIndex" || kind === "findPositionMatches") {
            let pending = observedWorkers.get(this);
            if (!pending) {
              pending = new Map();
              observedWorkers.set(this, pending);
              this.addEventListener("message", (event: MessageEvent) => {
                const reply = event.data as { id?: number; state?: string; computeMs?: number };
                const request = reply.id === undefined ? undefined : pending?.get(reply.id);
                if (!request) return;
                if (reply.state === "running") {
                  request.queueMs = performance.now() - request.startedAt;
                  return;
                }
                request.computeMs = reply.computeMs;
                request.roundtripMs = performance.now() - request.startedAt;
                if (request.kind === "findPositionMatches") {
                  const waitForPaint = () => {
                    if (document.querySelector(".similarity-panel")?.getAttribute("data-result-fen") === request.fen) {
                      requestAnimationFrame(() => {
                        request.paintMs = performance.now() - request.startedAt;
                      });
                    } else if (performance.now() - request.startedAt < 5_000) {
                      requestAnimationFrame(waitForPaint);
                    }
                  };
                  requestAnimationFrame(waitForPaint);
                }
                pending?.delete(reply.id!);
              });
            }
            const request = {
              kind,
              bytes: JSON.stringify(message).length,
              hasPositions: Reflect.has(task, "positions"),
              startedAt: performance.now(),
              fen: kind === "findPositionMatches" ? Reflect.get(task, "fen") : undefined,
            };
            observed.push(request);
            const requestId = typeof message === "object" && message !== null
              ? Reflect.get(message, "id") : undefined;
            if (typeof requestId === "number") pending.set(requestId, request);
          }
        }
        return Reflect.apply(originalPostMessage, this, [message, ...options]);
      },
    });
  });
  await prepareVisualUI(page, false, undefined, repertoireLines);
  await navigate(page, "Builder");
  await expect.poll(async () => page.evaluate(() =>
    (Reflect.get(window, "tempoStudyMessages") as Array<{ kind: string; roundtripMs?: number }>).filter(
      (message) => message.kind === "findPositionMatches" && message.roundtripMs !== undefined,
    ).length,
  )).toBeGreaterThan(0);
  const completedQueries = () => page.evaluate(() =>
    (Reflect.get(window, "tempoStudyMessages") as Array<{ kind: string; roundtripMs?: number }>).filter(
      (message) => message.kind === "findPositionMatches" && message.roundtripMs !== undefined,
    ).length,
  );
  const paintedQueries = () => page.evaluate(() =>
    (Reflect.get(window, "tempoStudyMessages") as Array<{ kind: string; paintMs?: number }>).filter(
      (message) => message.kind === "findPositionMatches" && message.paintMs !== undefined,
    ).length,
  );
  await expect.poll(paintedQueries).toBeGreaterThan(0);
  const boardFrame = page.locator(".board-frame");
  for (let repetition = 0; repetition < 3; repetition++) {
    const previousQueryCount = await completedQueries();
    const boardBounds = (await page.locator(".cg-wrap").boundingBox())!;
    for (const rank of [6, 4]) {
      await page.mouse.click(
        boardBounds.x + (4.5 * boardBounds.width) / 8,
        boardBounds.y + ((rank + 0.5) * boardBounds.height) / 8,
      );
    }
    await expect(boardFrame).toHaveAttribute("data-fen", /4P3/);
    await expect.poll(completedQueries).toBeGreaterThan(previousQueryCount);
    await expect.poll(paintedQueries).toBeGreaterThan(previousQueryCount);
    const afterMoveQueryCount = await completedQueries();
    await page.locator(".shared-board-toolbar .board-tools")
      .getByRole("button", { name: /Back/ }).click();
    await expect(boardFrame).not.toHaveAttribute("data-fen", /4P3/);
    await expect.poll(completedQueries).toBeGreaterThan(afterMoveQueryCount);
    await expect.poll(paintedQueries).toBeGreaterThan(afterMoveQueryCount);
  }
  const messages = await page.evaluate(() => Reflect.get(window, "tempoStudyMessages") as Array<{
    kind: string; bytes: number; hasPositions: boolean;
    queueMs?: number; computeMs?: number; roundtripMs?: number; paintMs?: number;
  }>);
  const querySamples = messages.filter((message) =>
    message.kind === "findPositionMatches" && message.roundtripMs !== undefined,
  ).map(({ bytes, queueMs, computeMs, roundtripMs, paintMs }) => ({
    bytes, queueMs: queueMs ?? null, computeMs: computeMs ?? null,
    roundtripMs: roundtripMs!, paintMs: paintMs ?? null,
  }));
  const indexSamples = messages.filter((message) =>
    message.kind === "initializePositionIndex" && message.roundtripMs !== undefined,
  ).map(({ bytes, queueMs, computeMs, roundtripMs }) => ({
    bytes, queueMs: queueMs ?? null, computeMs: computeMs ?? null,
    roundtripMs: roundtripMs!,
  }));
  const report = {
    schemaVersion: 1,
    timestamp: new Date().toISOString(),
    commit: process.env.TEMPO_COMMIT ?? process.env.GITHUB_SHA ?? null,
    fixture: { name: "legal-two-ply-opening-pairs-v1", lines: repertoireLines.length,
      plies: repertoireLines.length * 2, warmPositionChanges: 6 },
    indexSamples,
    querySamples,
    roundtripSummary: summarize(querySamples.map((sample) => sample.roundtripMs)),
    paintSummary: summarize(querySamples.map((sample) => sample.paintMs).filter(
      (duration): duration is number => duration !== null,
    )),
  };
  const reportBody = JSON.stringify(report, null, 2);
  writePerformanceReport("builder-similarity-chromium.json", reportBody);
  await testInfo.attach("builder-similarity-performance", {
    body: reportBody,
    contentType: "application/json",
  });
  expect(messages.some((message) => message.kind === "initializePositionIndex")).toBe(true);
  expect(indexSamples.length).toBeGreaterThan(0);
  for (const message of messages.filter((item) => item.kind === "findPositionMatches")) {
    expect(message.hasPositions).toBe(false);
    expect(message.bytes).toBeLessThan(512);
  }
  expect(querySamples.length).toBeGreaterThanOrEqual(7);
  expect(querySamples.every((sample) => sample.paintMs !== null)).toBe(true);
});

// #31: continuous held dragging is distinct from existing post-move metrics.
test("held-piece drag baseline separates workloads and capture overhead", async ({ browser }, testInfo) => {
  test.setTimeout(600_000);
  const { heldDrag, prepareHeldDrag, installHeldDiscovery, heldDragFixtureVersion, heldDragStartFen } = await import("./held-drag-fixtures");
  type Workload = "idle" | "synthetic-worker" | "discovery-preparation" | "stockfish-worker" | "main-thread-stall";
  type Run = {
    repetition: number; workload: Workload; enabled: boolean; cacheState: "cold-context" | "warm-context-reload";
    probe: import("./held-drag-fixtures").DragProbe; snapshot: import("./held-drag-fixtures").DragSnapshot | null;
    workloadEvidence: { startedAtMs: number | null; endedAtMs: number | null; error: string | null; messages: number };
  };
  const runs: Run[] = [];
  const report = {
    schemaVersion: 1, timestamp: new Date().toISOString(), commit: process.env.TEMPO_COMMIT ?? process.env.GITHUB_SHA ?? null,
    fixture: { name: heldDragFixtureVersion, pointerMoves: 40, paceMs: 20, repetitions: 3, dataset: "Spanish five-ply card and one discovery", probe: "bounded-transform-probe-v1" },
    environment: {
      browser: "chromium", browserVersion: browser.version(), buildMode: "production-local", runner: process.env.TEMPO_VISUAL_RUNNER,
      architecture: process.arch, viewport: { width: 1280, height: 800 }, devicePixelRatio: 1, reducedMotion: "no-preference",
      dockerCpus: process.env.TEMPO_DIAGNOSTIC_DOCKER_CPUS ?? "unavailable", dockerMemoryBytes: process.env.TEMPO_DIAGNOSTIC_DOCKER_MEMORY ?? "unavailable",
      host: process.env.TEMPO_DIAGNOSTIC_HOST ?? "unavailable", containerLimits: process.env.TEMPO_DIAGNOSTIC_CONTAINER_LIMITS ?? "unavailable",
      gpu: "unavailable; headless pinned browser", physicalPresentation: "unavailable", liveBackendLoad: "unmeasured; routed synthetic API fixtures",
    },
    measurement: "DOM transform at rAF; not presentation latency or INP; identical independent probe runs with capture on/off",
    runs, summaries: [] as Array<{ workload: Workload; enabled: boolean; count: number; frameSampleCount: number; displacementSampleCount: number; interruptionCount: number;
      frameGapMs: { p50: number; p95: number } | null; displacementCssPx: { p50: number; p95: number } | null }>,
  };
  function persist() { writePerformanceReport("held-drag-chromium.json", JSON.stringify(report, null, 2)); }
  try {
    persist();
    for (let repetition = 0; repetition < 3; repetition++) {
      for (const workload of ["idle", "synthetic-worker", "discovery-preparation", "stockfish-worker", "main-thread-stall"] as Workload[]) {
        // Alternate enabled/disabled order to reduce systematic warming bias.
        for (const enabled of repetition % 2 ? [false, true] : [true, false]) {
          const context = await browser.newContext({
            baseURL: testInfo.project.use.baseURL, viewport: { width: 1280, height: 800 },
            deviceScaleFactor: 1, reducedMotion: "no-preference", locale: "en-US", timezoneId: "America/New_York",
          });
          const experimentPage = await context.newPage();
          try {
            for (const cacheState of ["cold-context", "warm-context-reload"] as const) {
              let discovery!: Awaited<ReturnType<typeof installHeldDiscovery>>;
              if (cacheState === "cold-context") {
                await prepareHeldDrag(experimentPage, enabled, async () => { discovery = await installHeldDiscovery(experimentPage); });
              } else {
                await experimentPage.unroute("**/api/discoveries?**");
                await experimentPage.unroute("**/api/discoveries/*/recommendations");
                discovery = await installHeldDiscovery(experimentPage);
                await experimentPage.reload();
              }
              await expect(experimentPage.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
              await expect.poll(discovery.started).toBe(true);
              if (workload !== "discovery-preparation") {
                discovery.release(); await expect.poll(discovery.completed).toBe(true);
                // Settle the routed response before the idle/control measurements.
                await experimentPage.waitForTimeout(100);
              }
              await experimentPage.evaluate(() => {
                Object.assign(window, { tempoWorkloadEvidence: { startedAtMs: null, endedAtMs: null, error: null, messages: 0 } });
              });
              if (workload === "stockfish-worker") {
                // Initialize outside the measured hold. Work itself starts while held.
                await experimentPage.evaluate(() => new Promise<void>((resolve, reject) => {
                  const worker = new Worker("/stockfish-worker.js?v=4", { type: "module" });
                  Object.assign(window, { tempoHeldWorker: worker });
                  const timeout = setTimeout(() => reject(new Error("Stockfish fixture initialization timed out")), 30_000);
                  worker.onmessage = event => {
                    if (event.data.type === "ready") { clearTimeout(timeout); resolve(); }
                    if (event.data.type === "error") { clearTimeout(timeout); reject(new Error(event.data.message)); }
                  };
                  worker.postMessage({ type: "init" });
                }));
              }
              try {
                const result = await heldDrag(experimentPage, async step => {
                  if (step !== 10) return;
                  if (workload === "discovery-preparation") {
                    await experimentPage.evaluate(() => { Reflect.get(window, "tempoWorkloadEvidence").startedAtMs = performance.now(); });
                    discovery.release();
                  } else if (workload === "synthetic-worker") {
                    await experimentPage.evaluate(() => {
                      const evidence = Reflect.get(window, "tempoWorkloadEvidence");
                      const source = `onmessage=()=>{const end=performance.now()+1200;function slice(){const until=performance.now()+15;while(performance.now()<until){};if(performance.now()<end)setTimeout(slice,0);else postMessage('done')}slice()}`;
                      const url = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
                      const worker = new Worker(url); URL.revokeObjectURL(url);
                      Object.assign(window, { tempoHeldWorker: worker });
                      evidence.startedAtMs = performance.now();
                      worker.onmessage = () => { evidence.endedAtMs = performance.now(); evidence.messages++; };
                      worker.postMessage("start");
                    });
                  } else if (workload === "main-thread-stall") {
                    await experimentPage.evaluate(() => {
                      const evidence = Reflect.get(window, "tempoWorkloadEvidence");
                      evidence.startedAtMs = performance.now();
                      const until = performance.now() + 80;
                      while (performance.now() < until) { /* deliberate positive control */ }
                      evidence.endedAtMs = performance.now();
                    });
                  } else if (workload === "stockfish-worker") {
                    await experimentPage.evaluate(fen => {
                      const evidence = Reflect.get(window, "tempoWorkloadEvidence");
                      const worker = Reflect.get(window, "tempoHeldWorker") as Worker;
                      evidence.startedAtMs = performance.now();
                      worker.onmessage = event => {
                        evidence.messages++;
                        if (event.data.type === "error") { evidence.error = event.data.message; evidence.endedAtMs = performance.now(); }
                        if (event.data.line?.startsWith("bestmove ")) evidence.endedAtMs = performance.now();
                      };
                      worker.postMessage({ type: "analyze", id: 31, fen, depth: 12, multipv: 5 });
                    }, heldDragStartFen);
                  }
                });
                if (workload === "discovery-preparation") {
                  await expect.poll(discovery.completed).toBe(true);
                  await experimentPage.evaluate(() => { Reflect.get(window, "tempoWorkloadEvidence").endedAtMs = performance.now(); });
                }
                const workloadEvidence = await experimentPage.evaluate(() => Reflect.get(window, "tempoWorkloadEvidence")) as Run["workloadEvidence"];
                runs.push({ repetition, workload, enabled, cacheState, ...result, workloadEvidence });
                persist();
                expect(result.probe.sawDragging).toBe(true);
                expect(result.probe.samples.length).toBeGreaterThan(10);
                if (enabled) expect(result.snapshot?.sessions.length).toBeGreaterThan(0);
                else expect(result.snapshot).toBeNull();
                expect(workloadEvidence.error).toBeNull();
                if (workload === "stockfish-worker") expect(workloadEvidence.messages).toBeGreaterThan(0);
              } finally {
                discovery.release();
                await experimentPage.evaluate(() => (Reflect.get(window, "tempoHeldWorker") as Worker | undefined)?.terminate());
              }
            }
          } finally { await context.close(); }
        }
      }
    }
  } finally {
    for (const workload of ["idle", "synthetic-worker", "discovery-preparation", "stockfish-worker", "main-thread-stall"] as Workload[]) {
      for (const enabled of [true, false]) {
        const matching = runs.filter(run => run.workload === workload && run.enabled === enabled);
        const frameGaps = matching.flatMap(run => run.probe.samples.flatMap(sample => sample.gapMs === null ? [] : [sample.gapMs]));
        const displacements = matching.flatMap(run => run.probe.samples.flatMap(sample => sample.displacementCssPx === null ? [] : [sample.displacementCssPx]));
        report.summaries.push({ workload, enabled, count: matching.length, frameSampleCount: frameGaps.length, displacementSampleCount: displacements.length, interruptionCount: matching.filter(run => run.probe.interrupted).length,
          frameGapMs: frameGaps.length ? summarize(frameGaps) : null,
          displacementCssPx: displacements.length ? summarize(displacements) : null });
      }
    }
    persist();
    await testInfo.attach("held-drag-performance", { body: JSON.stringify(report, null, 2), contentType: "application/json" });
  }
  expect(runs).toHaveLength(60);
});
