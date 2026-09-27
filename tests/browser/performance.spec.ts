import { test, expect } from "./observability";
import { Chess } from "chess.js";
import { mkdirSync, writeFileSync } from "node:fs";
import { navigate } from "./ui-fixtures";
import { prepareVisualUI, type VisualRepertoireLine } from "./visual-fixtures";

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
  mkdirSync("test-results/performance", { recursive: true });
  writeFileSync(`test-results/performance/browser-${browserName}.json`, `${reportBody}\n`);
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
  mkdirSync("test-results/performance", { recursive: true });
  writeFileSync("test-results/performance/training-card-chromium.json", `${reportBody}\n`);
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
      kind: string; bytes: number; hasPositions: boolean; startedAt: number;
      queueMs?: number; computeMs?: number; roundtripMs?: number;
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
                pending?.delete(reply.id!);
              });
            }
            const request = {
              kind,
              bytes: JSON.stringify(message).length,
              hasPositions: Reflect.has(task, "positions"),
              startedAt: performance.now(),
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
    const afterMoveQueryCount = await completedQueries();
    await page.locator(".shared-board-toolbar .board-tools")
      .getByRole("button", { name: /Back/ }).click();
    await expect(boardFrame).not.toHaveAttribute("data-fen", /4P3/);
    await expect.poll(completedQueries).toBeGreaterThan(afterMoveQueryCount);
  }
  const messages = await page.evaluate(() => Reflect.get(window, "tempoStudyMessages") as Array<{
    kind: string; bytes: number; hasPositions: boolean;
    queueMs?: number; computeMs?: number; roundtripMs?: number;
  }>);
  const querySamples = messages.filter((message) =>
    message.kind === "findPositionMatches" && message.roundtripMs !== undefined,
  ).map(({ bytes, queueMs, computeMs, roundtripMs }) => ({
    bytes, queueMs: queueMs ?? null, computeMs: computeMs ?? null,
    roundtripMs: roundtripMs!,
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
  };
  const reportBody = JSON.stringify(report, null, 2);
  mkdirSync("test-results/performance", { recursive: true });
  writeFileSync("test-results/performance/builder-similarity-chromium.json", `${reportBody}\n`);
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
});
