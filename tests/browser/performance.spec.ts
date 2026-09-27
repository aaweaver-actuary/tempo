import { test, expect } from "./observability";
import { mkdirSync, writeFileSync } from "node:fs";
import { navigate } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";

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
  for (const rank of [6, 4])
    await page.mouse.click(
      boardBounds.x + (4.5 * boardBounds.width) / 8,
      boardBounds.y + ((rank + 0.5) * boardBounds.height) / 8,
    );
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3/);
  await page.evaluate(() => new Promise<void>((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
  ));
  const moveToPaintDuration = await page.evaluate(() =>
    performance.getEntriesByName("tempo:move-to-paint").at(-1)?.duration ?? null,
  );
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
    fixture: "prepareVisualUI-default",
    browser: browserName,
    viewSwitchSamples,
    viewSwitchSummary: Object.fromEntries(
      Object.entries(viewSwitchSamples).map(([mode, samples]) => [mode, summarize(samples)]),
    ),
    moveToPaintDuration,
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
  expect(moveToPaintDuration).not.toBeNull();
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

test("Builder similarity worker messages keep the position index in the worker", async ({ page }) => {
  await page.addInitScript(() => {
    const observed: Array<{ kind: string; bytes: number; hasPositions: boolean }> = [];
    Object.assign(window, { tempoStudyMessages: observed });
    const originalPostMessage = Worker.prototype.postMessage;
    Object.defineProperty(Worker.prototype, "postMessage", {
      configurable: true,
      value: function(this: Worker, message: unknown, ...options: unknown[]) {
        const task = typeof message === "object" && message !== null
          ? Reflect.get(message, "task") : undefined;
        if (task && typeof task === "object") {
          const kind = Reflect.get(task, "kind");
          if (kind === "initializePositionIndex" || kind === "findPositionMatches")
            observed.push({
              kind,
              bytes: JSON.stringify(message).length,
              hasPositions: Reflect.has(task, "positions"),
            });
        }
        return Reflect.apply(originalPostMessage, this, [message, ...options]);
      },
    });
  });
  await prepareVisualUI(page, false);
  await navigate(page, "Builder");
  await expect.poll(async () => page.evaluate(() =>
    (Reflect.get(window, "tempoStudyMessages") as Array<{ kind: string }>).filter(
      (message) => message.kind === "findPositionMatches",
    ).length,
  )).toBeGreaterThan(0);
  const messages = await page.evaluate(() => Reflect.get(window, "tempoStudyMessages") as Array<{
    kind: string; bytes: number; hasPositions: boolean;
  }>);
  expect(messages.some((message) => message.kind === "initializePositionIndex")).toBe(true);
  for (const message of messages.filter((item) => item.kind === "findPositionMatches")) {
    expect(message.hasPositions).toBe(false);
    expect(message.bytes).toBeLessThan(512);
  }
});
