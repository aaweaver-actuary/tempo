import type { Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";

async function squareCenter(page: Page, square: string) {
  const bounds = (await page.locator(".cg-wrap").boundingBox())!;
  const black = await page.locator(".board-frame").getAttribute("data-orientation") === "black";
  const file = square.charCodeAt(0) - 97;
  const rank = Number(square[1]) - 1;
  return { x: bounds.x + ((black ? 7 - file : file) + 0.5) * bounds.width / 8,
    y: bounds.y + ((black ? rank : 7 - rank) + 0.5) * bounds.height / 8 };
}
async function settledFrame(page: Page) {
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function followHeldPiece(page: Page, point: { x: number; y: number }) {
  await page.mouse.move(point.x, point.y, { steps: 4 });
  await expect(page.locator('piece.dragging[data-held-piece="true"]')).toHaveCount(1);
  await expect.poll(async () => {
    const bounds = await page.locator('piece.dragging[data-held-piece="true"]').boundingBox();
    return bounds ? Math.hypot(bounds.x + bounds.width / 2 - point.x, bounds.y + bounds.height / 2 - point.y) : Infinity;
  }).toBeLessThan(2);
}
async function counters(page: Page) {
  const bundle = JSON.parse(await page.getByRole("textbox", { name: "Debug information", includeHidden: true }).first().inputValue());
  return bundle.workspace.board.counters as Record<string, number>;
}

test("held training drag survives sync, service, notification and parent updates and drops once", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await prepareVisualUI(page, false);
  let syncResponses = 0;
  let activityResponses = 0;
  let reviewRequests = 0;
  await page.route("**/api/games/sync/status", (route) => {
    syncResponses += 1;
    return route.fulfill({ json: { providers: [], active_filters: { rated_only: true, speeds: ["blitz"], days: 30 + syncResponses } } });
  });
  await page.route("**/api/system/activity?*", (route) => {
    activityResponses += 1;
    return route.fulfill({ json: { items: [], counts: { running: 1, queued: 0, paused: 0, failed: 0 },
      total: 0, next_offset: null, writer: { healthy: true, foreground: activityResponses, background: 0 } } });
  });
  await page.route("**/api/cards/*/review", (route) => { reviewRequests += 1; return route.fulfill({ json: { persisted: true } }); });
  await page.reload();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
  await expect.poll(() => syncResponses).toBeGreaterThan(0);
  await expect.poll(() => activityResponses).toBeGreaterThan(0);
  // Populate the existing debug export UI, without adding a production test API.
  await page.evaluate(() => window.dispatchEvent(new ErrorEvent("error", {
    message: "Held-drag diagnostic fixture", error: new Error("Held-drag diagnostic fixture"),
  })));
  await page.getByRole("button", { name: "Notifications", exact: true }).click();
  await settledFrame(page);
  const baseline = await counters(page);
  const origin = await squareCenter(page, "e2");
  const destination = await squareCenter(page, "e4");
  await page.mouse.move(origin.x, origin.y);
  await page.mouse.down();
  await page.mouse.move(origin.x + 15, origin.y - 20, { steps: 4 });
  await expect(page.locator("piece.dragging")).toHaveCount(1);
  await page.locator("piece.dragging").evaluate((element) => element.setAttribute("data-held-piece", "true"));
  for (let update = 0; update < 4; update++) {
    const previousSync = syncResponses;
    const previousActivity = activityResponses;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => syncResponses).toBeGreaterThan(previousSync);
    await expect.poll(() => activityResponses).toBeGreaterThan(previousActivity);
    await page.evaluate(() => window.dispatchEvent(new Event("tempo:update-ready")));
    await settledFrame(page);
    await followHeldPiece(page, { x: origin.x + (update + 1) * 10, y: origin.y - (update + 1) * 15 });
  }
  await page.evaluate(() => window.dispatchEvent(new Event("tempo:update-ready")));
  await settledFrame(page);
  const afterUpdates = await counters(page);
  for (const name of ["acquisitions", "releases", "publications", "positionResets", "inputCancellations"])
    expect(afterUpdates[name] - baseline[name], name).toBe(0);
  await followHeldPiece(page, destination);
  await page.mouse.up();
  await expect(page.locator("piece.dragging")).toHaveCount(0);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3/);
  // The opponent's existing 420 ms response proves one drop advanced one step.
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4p3\/4P3/);
  expect(reviewRequests).toBe(0);
  const report = { measurement: "imperative operation counts during a real held drag; no latency claim",
    commit: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
    recordedAt: new Date().toISOString(), browser: testInfo.project.name, viewport: page.viewportSize(),
    updates: 4, syncResponses, activityResponses, before: baseline, after: afterUpdates,
    delta: Object.fromEntries(Object.keys(baseline).map((name) => [name, afterUpdates[name] - baseline[name]])) };
  const outputDirectory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
  mkdirSync(outputDirectory, { recursive: true });
  writeFileSync(join(outputDirectory, "held-drag-board-updates.json"), `${JSON.stringify(report, null, 2)}\n`);
  await testInfo.attach("held-drag-board-updates", { body: JSON.stringify(report, null, 2), contentType: "application/json" });
});

test("a read-only transition interrupts a real held drag and prevents a stale drop", async ({ page }) => {
  await prepareVisualUI(page, false);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
  const origin = await squareCenter(page, "e2");
  const destination = await squareCenter(page, "e4");
  const startingFen = await page.locator(".board-frame").getAttribute("data-fen");
  await page.mouse.move(origin.x, origin.y);
  await page.mouse.down();
  await page.mouse.move(origin.x + 20, origin.y - 30, { steps: 4 });
  await expect(page.locator("piece.dragging")).toHaveCount(1);
  // A real foreground queue failure must lock the board; sync status never does.
  await page.route("**/api/queue/window?*", (route) => route.fulfill({ status: 503, json: { detail: "Queue fixture unavailable" } }));
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "false");
  await expect(page.locator("piece.dragging")).toHaveCount(0);
  await page.mouse.move(destination.x, destination.y, { steps: 4 });
  await page.mouse.up();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", startingFen!);
});
