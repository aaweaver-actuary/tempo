import { test, expect, prepareUI, navigate, noPageOverflow } from "./ui-fixtures";
import { heldDrag, prepareHeldDrag } from "./held-drag-fixtures";
import { prepareVisualUI } from "./visual-fixtures";

test("phone notification history groups retries and opens to needs attention", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await page.addInitScript(() => {
    const base = { occurredAt: "2026-10-02T09:00:00Z", updatedAt: "2026-10-02T09:00:00Z", resolvedAt: null, active: false };
    localStorage.setItem("tempo-notifications-v1", JSON.stringify([
      ...Array.from({ length: 20 }, (_, index) => ({ ...base, id: `retry-${index}`, severity: "warning", source: "training queue", message: "Queue sync paused. Reconnect to retry." })),
      { ...base, id: "saved", severity: "success", source: "review", message: "Routine review saved." },
      { ...base, id: "resolved", severity: "error", source: "service", message: "Recovered service failure.", resolvedAt: "2026-10-02T09:01:00Z" },
    ]));
  });
  await prepareUI(page);
  await page.getByRole("button", { name: "Notifications" }).click();
  await expect(page.getByRole("button", { name: "Needs attention" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".notification-item").filter({ hasText: "Queue sync paused." })).toHaveCount(1);
  await expect(page.locator(".notification-list")).toContainText("Repeated 20 times");
  await expect(page.locator(".notification-list")).not.toContainText("Routine review saved.");
  await expect(page.locator(".notification-list")).not.toContainText("Recovered service failure.");
  await page.locator(".notification-tray").screenshot({ path: testInfo.outputPath("notification-needs-attention.png") });
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page.locator(".notification-list")).toContainText("Routine review saved.");
  await expect(page.locator(".notification-list")).toContainText("Recovered service failure.");
  await page.locator(".notification-tray").screenshot({ path: testInfo.outputPath("notification-all-history.png") });
  await noPageOverflow(page);
});

for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 1280, height: 720 }]) {
  test(`activity tray stays reachable and controls queued work ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route("**/api/system/activity?**", route => route.fulfill({ json: {
      items: [{ source: "durable", id: "fixture-task", title: "Opening graph rebuild",
        state: "queued", phase: "Waiting to rebuild", completed: null, total: null,
        updated_at: "2026-09-22T00:00:00Z", error: null, paused: false, promoted: false }],
      counts: { running: 0, queued: 1, paused: 0, failed: 0 }, total: 1, next_offset: null,
    } }));
    let controlled = false;
    await page.route("**/api/system/activity/control", route => {
      controlled = true;
      return route.fulfill({ json: { ok: true } });
    });
    await prepareUI(page);
    const trigger = page.getByRole("button", { name: /Analysis activity/ });
    await expect(trigger).toBeVisible();
    await trigger.click();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    const panelBounds = (await page.locator("#tempo-activity-content").boundingBox())!;
    expect(panelBounds.x).toBeGreaterThanOrEqual(0);
    expect(panelBounds.x + panelBounds.width).toBeLessThanOrEqual(viewport.width);
    await expect(page.getByText("Opening graph rebuild")).toBeVisible();
    await expect(page.getByRole("progressbar", { name: "Opening graph rebuild progress" })).toHaveAttribute("aria-valuetext", "Queued");
    await page.getByRole("button", { name: "Prioritize" }).click();
    await expect.poll(() => controlled).toBe(true);
    await page.getByRole("button", { name: "Close" }).click();
    await navigate(page, "Progress");
    await expect(trigger).toBeVisible();
    await noPageOverflow(page);
  });
}

test("analysis activity count growth keeps desktop navigation anchored", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 900 });
  let queuedCount = 9;
  await page.route("**/api/system/activity?**", route => route.fulfill({ json: {
    items: [], counts: { running: 0, queued: queuedCount, paused: 0, failed: 0 },
    total: queuedCount, next_offset: null,
  } }));
  await prepareUI(page);
  const train = page.getByRole("navigation", { name: "Primary navigation" })
    .getByRole("button", { name: "Train", exact: true }).filter({ visible: true });
  const initial = (await train.boundingBox())!;
  queuedCount = 1773;
  // Wake the closed trigger explicitly; periodic health/count freshness is now 30 seconds.
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("button", { name: "Analysis activity" })).toContainText("1773", { timeout: 5000 });
  const grown = (await train.boundingBox())!;
  expect(Math.abs(grown.x - initial.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(grown.y - initial.y)).toBeLessThanOrEqual(1);
  const navigationEnd = (await page.getByRole("navigation", { name: "Primary navigation" }).boundingBox())!.x
    + (await page.getByRole("navigation", { name: "Primary navigation" }).boundingBox())!.width;
  const activityStart = (await page.getByRole("button", { name: "Analysis activity" }).boundingBox())!.x;
  expect(navigationEnd).toBeLessThan(activityStart);
  await noPageOverflow(page);
});

test("notifications tray remains inside a 320px phone viewport", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await prepareUI(page);
  await page.getByRole("button", { name: "Notifications" }).click();
  const tray = (await page.locator(".notification-tray").boundingBox())!;
  expect(tray.x).toBeGreaterThanOrEqual(0);
  expect(tray.x + tray.width).toBeLessThanOrEqual(320);
  await expect(page.getByRole("button", { name: "Copy JSON" })).toBeVisible();
});

for (const width of [320, 1280]) {
  test(`notification clear controls preserve history across reload and count new arrivals at ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 });
    await page.addInitScript(() => {
      if (localStorage.getItem("tempo-notifications-v1")) return;
      localStorage.setItem("tempo-notifications-v1", JSON.stringify([
        { id: "clear-error", key: "clear-error", severity: "error", source: "service",
          message: "Clear fixture service failure", occurredAt: "2026-09-18T14:00:00.000Z",
          updatedAt: "2026-09-18T14:00:00.000Z", resolvedAt: null, active: false,
          occurrenceCount: 3, details: { status: 503 } },
        { id: "clear-warning", key: "clear-warning", severity: "warning", source: "sync",
          message: "Clear fixture saved conflict", occurredAt: "2026-09-18T13:00:00.000Z",
          updatedAt: "2026-09-18T13:00:00.000Z", resolvedAt: null, active: false },
        { id: "clear-info", severity: "info", source: "queue", message: "Clear fixture queue ready",
          occurredAt: "2026-09-18T12:00:00.000Z", updatedAt: "2026-09-18T12:00:00.000Z",
          resolvedAt: null, active: false },
      ]));
    });
    await prepareVisualUI(page);
    const notificationTrigger = page.getByRole("button", { name: "Notifications", exact: true });
    const countBadge = notificationTrigger.locator(".notification-count");
    await expect(countBadge).toHaveText("2");
    const studyBoard = page.locator(".persistent-board-shell .board-viewport");
    await expect(studyBoard).toBeVisible();
    const originalBoardBounds = (await studyBoard.boundingBox())!;
    await notificationTrigger.click();
    const notificationTray = page.getByRole("region", { name: "Notifications", exact: true });
    const errorEntry = notificationTray.getByRole("article").filter({ hasText: "Clear fixture service failure" });
    await expect(errorEntry.getByText("New", { exact: true })).toBeVisible();
    const individualClear = errorEntry.getByRole("button", { name: "Clear", exact: true });
    const clearButtonBounds = (await individualClear.boundingBox())!;
    expect(clearButtonBounds.height).toBeGreaterThanOrEqual(width < 768 ? 44 : 36);
    await individualClear.focus();
    await individualClear.press("Enter");
    await expect(errorEntry).toHaveCount(0);
    await notificationTray.getByRole("button", { name: "All", exact: true }).click();
    await expect(errorEntry.getByText("Cleared", { exact: true })).toBeVisible();
    await expect(countBadge).toHaveText("1");
    const boardAfterIndividualClear = (await studyBoard.boundingBox())!;
    for (const coordinate of ["x", "y", "width", "height"] as const)
      expect(Math.abs(boardAfterIndividualClear[coordinate] - originalBoardBounds[coordinate])).toBeLessThanOrEqual(1);
    await notificationTray.getByRole("button", { name: "error", exact: true }).click();
    await notificationTray.getByRole("button", { name: "Clear all", exact: true }).click();
    await expect(countBadge).toHaveCount(0);
    await expect(notificationTray.getByRole("button", { name: "Clear all", exact: true })).toBeDisabled();
    await notificationTray.getByRole("button", { name: "All", exact: true }).click();
    await expect(notificationTray.getByRole("article")).toHaveCount(3);
    await expect(notificationTray.getByText("Cleared", { exact: true })).toHaveCount(3);
    const trayBounds = (await notificationTray.boundingBox())!;
    expect(trayBounds.x).toBeGreaterThanOrEqual(0);
    expect(trayBounds.x + trayBounds.width).toBeLessThanOrEqual(width);
    await noPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`cleared-notifications-${width}.png`), fullPage: true });
    await page.reload();
    await expect(countBadge).toHaveCount(0);
    await notificationTrigger.click();
    await expect(notificationTray.getByRole("article")).toHaveCount(0);
    await notificationTray.getByRole("button", { name: "All", exact: true }).click();
    await expect(notificationTray.getByText("Cleared", { exact: true })).toHaveCount(3);
    await page.evaluate(() => window.dispatchEvent(new ErrorEvent("error", {
      message: "New notification after clearing", error: new Error("New notification after clearing"),
    })));
    await expect(countBadge).toHaveText("1");
    const newEntry = notificationTray.getByRole("article").filter({ hasText: "New notification after clearing" });
    await expect(newEntry.getByText("New", { exact: true })).toBeVisible();
    await expect(newEntry.getByRole("button", { name: "Clear", exact: true })).toBeVisible();
    await expect(notificationTray.getByText("Cleared", { exact: true })).toHaveCount(3);
    const finalBoardBounds = (await studyBoard.boundingBox())!;
    for (const coordinate of ["x", "y", "width", "height"] as const)
      expect(Math.abs(finalBoardBounds[coordinate] - originalBoardBounds[coordinate])).toBeLessThanOrEqual(1);
    await noPageOverflow(page);
  });
}

for (const width of [390, 1280]) {
  test(`analysis activity count fits header controls at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.route("**/api/system/activity?**", route => route.fulfill({ json: {
      items: [], counts: { running: 0, queued: 1773, paused: 0, failed: 0 },
      total: 1773, next_offset: null,
    } }));
    await prepareUI(page);
    await expect(page.getByRole("button", { name: "Analysis activity" })).toContainText("1773");
    const brand = (await page.getByRole("button", { name: "Tempo home" }).boundingBox())!;
    const actions = (await page.locator(".top-actions").boundingBox())!;
    expect(brand.x + brand.width).toBeLessThanOrEqual(actions.x);
    if (width >= 1100) {
      const navigation = (await page.getByRole("navigation", { name: "Primary navigation" }).boundingBox())!;
      expect(navigation.x + navigation.width).toBeLessThanOrEqual(actions.x);
      const lastDestination = (await page.getByRole("navigation", { name: "Primary navigation" })
        .getByRole("button", { name: "Settings", exact: true }).filter({ visible: true }).boundingBox())!;
      expect(lastDestination.x + lastDestination.width).toBeLessThanOrEqual(actions.x);
    }
    await noPageOverflow(page);
  });
}

test("status_recovery_during_training_preserves_held_drag_and_command_execution", async ({ page }, testInfo) => {
  let releaseInitialStatus: (() => void) | undefined;
  let syncRequests = 0; let activityRequests = 0; let controlRequests = 0;
  await prepareHeldDrag(page, true, async () => {
    await page.route("**/api/games/sync/status", async route => {
      syncRequests++;
      if (syncRequests === 1) await new Promise<void>(resolve => { releaseInitialStatus = resolve; });
      await route.fulfill({ json: { providers: [], active_filters: { rated_only: true, speeds: ["blitz"], days: 30 + syncRequests } } });
    });
    await page.route("**/api/system/activity?**", route => {
      activityRequests++;
      return route.fulfill({ json: {
        items: [{ source: "durable", id: "status-drag-task", title: "Status drag task", state: "running", phase: "Working",
          completed: 1, total: 10, updated_at: "2026-09-22T00:00:00Z", error: null, paused: false, promoted: false }],
        counts: { running: 1, queued: 0, paused: 0, failed: 0 }, total: 1, next_offset: null,
      } });
    });
    await page.route("**/api/system/activity/control", route => {
      controlRequests++;
      return route.fulfill({ json: { ok: true } });
    });
  });
  try {
    await expect.poll(() => !!releaseInitialStatus && activityRequests > 0).toBe(true);
    const result = await heldDrag(page, async step => {
      if (step === 10) {
        await page.evaluate(() => {
          window.dispatchEvent(new Event("focus"));
          window.dispatchEvent(new Event("online"));
          document.dispatchEvent(new Event("visibilitychange"));
        });
        // While the original response is held, the burst cannot launch parallel status reads.
        expect(syncRequests).toBe(1);
        releaseInitialStatus?.();
        await expect.poll(() => syncRequests).toBeGreaterThanOrEqual(2);
      }
    });
    expect(result.probe.sawDragging).toBe(true);
    expect(result.probe.interrupted).toBe(false);
    expect(result.snapshot?.sessions.at(-1)?.endReason).toBeNull();
    await page.getByRole("button", { name: "Analysis activity" }).click();
    await expect(page.getByText("Status drag task")).toBeVisible();
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect.poll(() => controlRequests).toBe(1);
    await testInfo.attach("status-polling-training", { body: JSON.stringify({ syncRequests, activityRequests, controlRequests,
      probe: result.probe, snapshot: result.snapshot }), contentType: "application/json" });
  } finally { releaseInitialStatus?.(); }
});

// Real PostgreSQL visibility and receipt workflow; no activity/command routes mocked.
test("clear finished archives across browser devices while retaining failures, pauses and later completions", async ({ page, browser }) => {
  const { execFileSync } = await import("node:child_process");
  const { randomUUID } = await import("node:crypto");
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("Owned PostgreSQL runner required");
  const identity = `activity-history-proof-${randomUUID()}`;
  const fixture = (action: string) => execFileSync("docker", ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml", "run", "--rm", "--no-deps", "schema", "python", "/source/scripts/check_postgres_activity_history.py", action, identity], { encoding: "utf8", timeout: 30_000 });
  const secondDevice = await browser.newContext({ baseURL: test.info().project.use.baseURL, viewport: { width: 390, height: 844 } });
  try {
    fixture("seed");
    await page.setViewportSize({ width: 1280, height: 844 });
    await prepareUI(page);
    await page.getByRole("button", { name: "Analysis activity", exact: true }).click();
    const clear = page.getByRole("button", { name: "Clear finished" });
    await expect(clear).toBeEnabled();
    await clear.click();
    await expect.poll(async () => page.evaluate(() => localStorage.getItem("tempo-pending-activity-clear-v1"))).toBeNull();
    await page.getByRole("combobox", { name: "Activity group" }).selectOption("history");
    await page.getByText(/^History ·/).click();
    await expect(page.getByText("Activity Fixture Finished", { exact: true })).toBeVisible();
    const other = await secondDevice.newPage();
    await prepareUI(other);
    await other.getByRole("button", { name: "Analysis activity", exact: true }).click();
    await other.getByRole("combobox", { name: "Activity group" }).selectOption("history");
    await other.getByText(/^History ·/).click();
    await expect(other.getByText("Activity Fixture Finished", { exact: true })).toBeVisible();
    await other.getByRole("combobox", { name: "Activity group" }).selectOption("needs_attention");
    await expect(other.getByText("Activity Fixture Failed", { exact: true })).toBeVisible();
    await other.getByRole("combobox", { name: "Activity group" }).selectOption("paused");
    await other.getByText(/^Manually paused ·/).click();
    await expect(other.getByText("Activity Fixture Paused", { exact: true })).toBeVisible();
    fixture("finish-later");
    await other.getByRole("combobox", { name: "Activity group" }).selectOption("finished");
    await other.getByText(/^Finished ·/).click();
    await expect(other.getByText("Activity Fixture Later", { exact: true })).toBeVisible();
    await expect(other.getByRole("button", { name: "Clear finished" })).toBeEnabled();
    await noPageOverflow(page); await noPageOverflow(other);
  } finally { await secondDevice.close(); fixture("cleanup"); }
});

test("durable analysis incidents survive reload, link to affected work and notify verified recovery once", async ({ page, browser }) => {
  const { execFileSync } = await import("node:child_process");
  const { randomUUID } = await import("node:crypto");
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("The isolated PostgreSQL browser runner is required");
  const identity = `activity-health-proof-${randomUUID()}`;
  const fixture = (action: string) => execFileSync("docker", ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml", "run", "--rm", "--no-deps", "schema", "python", "/source/scripts/check_postgres_activity_health.py", action, identity], { encoding: "utf8", timeout: 30_000 });
  fixture("seed");
  const otherDevice = await browser.newContext({ baseURL: test.info().project.use.baseURL });
  try {
    await prepareUI(page); await navigate(page, "Builder");
    await expect(page.locator(".notification-toast").filter({ hasText: "five consecutive identical transaction timeouts" })).toHaveCount(1);
    await page.getByRole("button", { name: "Notifications", exact: true }).click();
    const incident = page.locator(".notification-item").filter({ hasText: "five consecutive identical transaction timeouts" });
    await expect(incident).toHaveCount(1);
    await incident.getByRole("button", { name: "View analysis" }).click();
    const panel = page.locator("#tempo-activity-content");
    await expect(panel.getByRole("button", { name: "Show all analysis" })).toBeVisible();
    await expect(panel.locator(".tempo-activity-item")).toHaveCount(1);
    await expect(panel).toContainText("Needs attention");
    await page.reload(); await navigate(page, "Builder");
    await expect(page.locator(".notification-toast").filter({ hasText: "five consecutive identical transaction timeouts" })).toHaveCount(0);
    await page.getByRole("button", { name: "Notifications", exact: true }).click();
    await expect(incident).toHaveCount(1);
    const second = await otherDevice.newPage();
    await prepareUI(second); await navigate(second, "Builder");
    await second.getByRole("button", { name: "Notifications", exact: true }).click();
    await expect(second.locator(".notification-item").filter({ hasText: "five consecutive identical transaction timeouts" })).toHaveCount(1);
    fixture("recover");
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect(page.locator(".notification-toast").filter({ hasText: "making progress again" })).toHaveCount(1);
    await page.locator(".notification-tray").getByRole("button", { name: "All", exact: true }).click();
    await expect(page.locator(".notification-item").filter({ hasText: "making progress again" })).toHaveCount(1);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect(page.locator(".notification-toast").filter({ hasText: "making progress again" })).toHaveCount(1);
    await page.reload();
    await expect(page.locator(".notification-toast").filter({ hasText: "making progress again" })).toHaveCount(0);
  } finally { await otherDevice.close(); fixture("cleanup"); }
});
