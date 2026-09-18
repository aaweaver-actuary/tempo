import { test, expect } from "./observability";
import { navigate } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
test("warm workspace shells paint within 200ms p95 without long interaction tasks", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await prepareVisualUI(page, false);
  const modes = ["Builder", "Games", "Endgames", "Tactics", "Train"];
  for (const mode of modes) await navigate(page, mode);
  const durations: number[] = [];
  for (let iteration = 0; iteration < 3; iteration++)
    for (const mode of modes) {
      await navigate(page, mode);
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      durations.push(
        await page.evaluate(
          () =>
            performance.getEntriesByName("tempo:view-switch").at(-1)!.duration,
        ),
      );
    }
  await navigate(page, "Builder");
  const separator = page.getByRole("separator", { name: "Board size" });
  await separator.focus();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
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
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  const longTasks = await page.evaluate(
    () => Reflect.get(window, "tempoInteractionTasks") as number[],
  );
  await testInfo.attach("interaction-performance", {
    body: JSON.stringify({ durations, longTasks }, null, 2),
    contentType: "application/json",
  });
  const sorted = durations.toSorted((left, right) => left - right);
  expect(sorted[Math.ceil(sorted.length * 0.95) - 1]).toBeLessThanOrEqual(200);
  expect(longTasks.filter((duration) => duration > 100)).toEqual([]);
});
