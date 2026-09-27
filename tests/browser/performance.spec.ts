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
  };
  const reportBody = JSON.stringify(report, null, 2);
  mkdirSync("test-results/performance", { recursive: true });
  writeFileSync(`test-results/performance/browser-${browserName}.json`, `${reportBody}\n`);
  await testInfo.attach("interaction-performance", {
    body: reportBody,
    contentType: "application/json",
  });
  expect(moveToPaintDuration).not.toBeNull();
  for (const samples of Object.values(viewSwitchSamples))
    expect(summarize(samples).p95).toBeLessThanOrEqual(300);
  expect(longTasks.filter((duration) => duration > 100)).toEqual([]);
});
