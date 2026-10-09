import { test, expect, prepareUI, navigate, noPageOverflow } from "./ui-fixtures";
import { heldDrag, prepareHeldDrag } from "./held-drag-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
import type { Page, TestInfo } from "@playwright/test";

async function notificationLayoutGeometry(page: Page) {
  return page.evaluate(() => {
    const bounds = (selector: string) => {
      const rectangle = document.querySelector(selector)?.getBoundingClientRect();
      return rectangle ? { x: rectangle.x, y: rectangle.y, width: rectangle.width, height: rectangle.height } : null;
    };
    return {
      board: bounds(".persistent-board-shell .board-viewport"),
      heading: bounds(".shared-board-heading"),
      phoneHeading: bounds(".phone-study-heading"),
      header: bounds(".topbar"),
      topActions: bounds(".top-actions"),
      fonts: document.fonts.status,
    };
  });
}

async function recordNotificationGeometry(page: Page, testInfo: TestInfo, label: string) {
  const geometry = await notificationLayoutGeometry(page);
  await testInfo.attach(`notification-geometry-${label}`, {
    body: JSON.stringify(geometry), contentType: "application/json",
  });
  return geometry;
}

async function readyNotificationStudyBounds(page: Page) {
  const studyBoard = page.locator(".persistent-board-shell .board-viewport");
  // The persistent board and notification controls mount before queue/worker hydration.
  await expect(page.getByRole("heading", { name: "Spanish opening", exact: true })).toBeVisible();
  await expect(page.locator(".persistent-board-shell")).toHaveAttribute("data-board-owner", "train");
  await expect(page.locator(".persistent-board-shell")).toHaveAttribute("data-unavailable", "false");
  await expect(studyBoard.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
  await expect(studyBoard).toBeVisible();
  if (page.viewportSize()!.width < 768) {
    await expect(page.locator(".shared-board-heading .phone-study-heading")).toContainText("white to play");
  }
  await expect.poll(() => page.evaluate(() => document.fonts.status)).toBe("loaded");
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  let previousGeometry = "";
  let consecutiveStableFrames = 0;
  let latestGeometry: Awaited<ReturnType<typeof notificationLayoutGeometry>> | undefined;
  await expect.poll(async () => {
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    latestGeometry = await notificationLayoutGeometry(page);
    const currentGeometry = JSON.stringify(latestGeometry);
    consecutiveStableFrames = currentGeometry === previousGeometry ? consecutiveStableFrames + 1 : 1;
    previousGeometry = currentGeometry;
    return latestGeometry.board!.width > 0 && latestGeometry.board!.height > 0 &&
      latestGeometry.fonts === "loaded" && consecutiveStableFrames >= 3;
  }, { timeout: 5000, intervals: [0], message: "loaded notification study layout must settle across three animation frames" }).toBe(true);
  return latestGeometry!.board!;
}

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

test("issue137_notification_geometry_waits_for_training_heading_after_delayed_reload", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await prepareVisualUI(page);
  const phoneHeading = page.locator(".shared-board-heading .phone-study-heading");
  await expect(phoneHeading.getByRole("heading", { name: "Spanish opening", exact: true })).toBeVisible();
  const originalBoardBounds = await readyNotificationStudyBounds(page);
  const headingHeight = (await phoneHeading.boundingBox())!.height;
  let releaseQueueResponse!: () => void;
  let markQueueRequested!: () => void;
  const queueResponseReleased = new Promise<void>(resolve => { releaseQueueResponse = resolve; });
  const queueRequested = new Promise<void>(resolve => { markQueueRequested = resolve; });
  await page.route("**/api/queue/window?**", async route => {
    markQueueRequested();
    await queueResponseReleased;
    await route.fallback();
  });
  let readyBounds: ReturnType<typeof readyNotificationStudyBounds> | undefined;
  try {
    await page.reload();
    await queueRequested;
    await expect(phoneHeading).toHaveCount(0);
    await expect(page.locator(".persistent-board-shell")).toHaveAttribute("data-unavailable", "true");
    await recordNotificationGeometry(page, testInfo, "delayed-reload-unready");
    const unreadyBoardBounds = (await page.locator(".persistent-board-shell .board-viewport").boundingBox())!;
    expect(Math.abs(originalBoardBounds.y - unreadyBoardBounds.y - headingHeight)).toBeLessThanOrEqual(1);
    await testInfo.attach("delayed-reload-board-geometry", {
      body: JSON.stringify({ originalBoardBounds, unreadyBoardBounds, headingHeight }), contentType: "application/json",
    });
    let readinessCompleted = false;
    readyBounds = readyNotificationStudyBounds(page).then(bounds => { readinessCompleted = true; return bounds; });
    // Notification controls are usable while the training response is still held.
    await page.getByRole("button", { name: "Notifications", exact: true }).click();
    await expect(page.getByRole("region", { name: "Notifications", exact: true })).toBeVisible();
    expect(readinessCompleted, "geometry readiness must wait for the training heading").toBe(false);
    releaseQueueResponse();
    const reloadedBoardBounds = await readyBounds;
    await recordNotificationGeometry(page, testInfo, "delayed-reload-ready");
    for (const coordinate of ["x", "y", "width", "height"] as const)
      expect(Math.abs(reloadedBoardBounds[coordinate] - originalBoardBounds[coordinate])).toBeLessThanOrEqual(1);
    await noPageOverflow(page);
  } finally {
    releaseQueueResponse();
    await readyBounds;
  }
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
    const originalBoardBounds = await readyNotificationStudyBounds(page);
    await recordNotificationGeometry(page, testInfo, "initial-ready");
    await notificationTrigger.click();
    await recordNotificationGeometry(page, testInfo, "opened");
    const notificationTray = page.getByRole("region", { name: "Notifications", exact: true });
    const errorEntry = notificationTray.getByRole("article").filter({ hasText: "Clear fixture service failure" });
    await expect(errorEntry.getByText("New", { exact: true })).toBeVisible();
    const individualClear = errorEntry.getByRole("button", { name: "Clear", exact: true });
    const clearButtonBounds = (await individualClear.boundingBox())!;
    expect(clearButtonBounds.height).toBeGreaterThanOrEqual(width < 768 ? 44 : 36);
    await individualClear.focus();
    await individualClear.press("Enter");
    await expect(errorEntry).toHaveCount(0);
    await recordNotificationGeometry(page, testInfo, "individual-clear");
    await notificationTray.getByRole("button", { name: "All", exact: true }).click();
    await expect(errorEntry.getByText("Cleared", { exact: true })).toBeVisible();
    await expect(countBadge).toHaveText("1");
    await recordNotificationGeometry(page, testInfo, "individual-clear-all-filter");
    const boardAfterIndividualClear = (await studyBoard.boundingBox())!;
    for (const coordinate of ["x", "y", "width", "height"] as const)
      expect(Math.abs(boardAfterIndividualClear[coordinate] - originalBoardBounds[coordinate])).toBeLessThanOrEqual(1);
    await notificationTray.getByRole("button", { name: "error", exact: true }).click();
    await recordNotificationGeometry(page, testInfo, "error-filter");
    await notificationTray.getByRole("button", { name: "Clear all", exact: true }).click();
    await expect(countBadge).toHaveCount(0);
    await recordNotificationGeometry(page, testInfo, "clear-all");
    await expect(notificationTray.getByRole("button", { name: "Clear all", exact: true })).toBeDisabled();
    await notificationTray.getByRole("button", { name: "All", exact: true }).click();
    await expect(notificationTray.getByRole("article")).toHaveCount(3);
    await expect(notificationTray.getByText("Cleared", { exact: true })).toHaveCount(3);
    await recordNotificationGeometry(page, testInfo, "cleared-history");
    const trayBounds = (await notificationTray.boundingBox())!;
    expect(trayBounds.x).toBeGreaterThanOrEqual(0);
    expect(trayBounds.x + trayBounds.width).toBeLessThanOrEqual(width);
    await noPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`cleared-notifications-${width}.png`), fullPage: true });
    await page.reload();
    await recordNotificationGeometry(page, testInfo, "reload-before-readiness");
    const reloadedBoardBounds = await readyNotificationStudyBounds(page);
    await recordNotificationGeometry(page, testInfo, "reload-ready");
    for (const coordinate of ["x", "y", "width", "height"] as const)
      expect(Math.abs(reloadedBoardBounds[coordinate] - originalBoardBounds[coordinate])).toBeLessThanOrEqual(1);
    await expect(countBadge).toHaveCount(0);
    await notificationTrigger.click();
    await recordNotificationGeometry(page, testInfo, "reload-opened");
    await expect(notificationTray.getByRole("article")).toHaveCount(0);
    await notificationTray.getByRole("button", { name: "All", exact: true }).click();
    await expect(notificationTray.getByText("Cleared", { exact: true })).toHaveCount(3);
    await recordNotificationGeometry(page, testInfo, "reload-cleared-history");
    await page.evaluate(() => window.dispatchEvent(new ErrorEvent("error", {
      message: "New notification after clearing", error: new Error("New notification after clearing"),
    })));
    await expect(countBadge).toHaveText("1");
    await recordNotificationGeometry(page, testInfo, "new-arrival");
    const newEntry = notificationTray.getByRole("article").filter({ hasText: "New notification after clearing" });
    await expect(newEntry.getByText("New", { exact: true })).toBeVisible();
    await expect(newEntry.getByRole("button", { name: "Clear", exact: true })).toBeVisible();
    await expect(notificationTray.getByText("Cleared", { exact: true })).toHaveCount(3);
    await recordNotificationGeometry(page, testInfo, "final");
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
