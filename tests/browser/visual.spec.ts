import { test, expect } from "./observability";
import { navigate } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
import type { ActivityResponse } from "../../app/lib/service-status";
for (const viewport of [
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1280, height: 720 },
  { width: 1920, height: 1080 },
]) {
  for (const workspace of [
    "Train",
    "Tactics",
    "Endgames",
    "Repertoire",
    "Builder",
    "Games",
    "Progress",
    "Statistics",
    "Settings",
  ]) {
    test(`${workspace} ${viewport.width}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await prepareVisualUI(page);
      if (workspace === "Endgames")
        await page.evaluate(() => {
          Math.random = () => 0.5;
        });
      await navigate(page, workspace);
      if (workspace === "Endgames")
        await expect(page.locator(".board-frame")).toHaveAttribute(
          "data-fen",
          "8/8/8/7R/K7/8/8/6k1 w - - 0 1",
        );
      await page.evaluate(() => document.fonts.ready);
      if (workspace === "Train") {
        await expect(
          page.getByText("Spanish opening", { exact: true }).first(),
        ).toBeVisible();
        await expect(page.getByText("Phone queue prepared for 2026-09-18.")).toHaveCount(0);
      }
      if (workspace === "Tactics")
        await expect(page.getByText(/Puzzle \d+ of/)).toBeVisible();
      if (workspace === "Builder")
        await expect(
          page.getByRole("combobox", { name: "Active repertoire" }),
        ).toHaveValue("visual-repertoire");
      if (workspace === "Games") {
        await expect(page.locator(".games-page")).toBeVisible({ timeout: 15000 });
      }
      if (workspace === "Games")
        await expect(
          page.getByRole("heading", { name: "Spanish opening", exact: true }),
        ).toBeVisible();
      await expect(page).toHaveScreenshot(
        `${workspace.toLowerCase()}-${viewport.width}.png`,
        { animations: "disabled", fullPage: true },
      );
    });
  }
}

for (const width of [390, 1280]) {
  test(`Repertoire statistics ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 720 });
    await prepareVisualUI(page);
    await navigate(page, "Repertoire");
    await page.getByRole("button", { name: "Statistics", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Prefix cards" })).toBeVisible();
    await expect(page.locator(".repertoire-position-board .board-frame")).toBeVisible();
    await expect(page).toHaveScreenshot(`repertoire-statistics-${width}.png`, {
      animations: "disabled", fullPage: true,
    });
  });
}
test("service-unavailable", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/progress", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Local database unavailable. Check the data mount." },
    }),
  );
  await navigate(page, "Progress");
  await expect(page.locator(".ui-notice.error")).toContainText(
    "Review history unavailable: Could not load /api/progress (HTTP 503)",
  );
  await expect(page).toHaveScreenshot("service-unavailable.png");
});

test("laptop header navigation and actions remain separate", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await prepareVisualUI(page);
  await navigate(page, "Train");
  const navigationControl = await page.locator(".desktop-navigation").isVisible()
    ? page.locator(".desktop-navigation button").last()
    : page.locator(".tablet-navigation");
  const navigationBounds = await navigationControl.boundingBox();
  const actionBounds = await page.locator(".top-actions").boundingBox();
  expect(navigationBounds).not.toBeNull();
  expect(actionBounds).not.toBeNull();
  expect(navigationBounds!.x + navigationBounds!.width).toBeLessThanOrEqual(actionBounds!.x);
});

for (const viewport of [{ width: 390, height: 844 }, { width: 1280, height: 720 }]) {
  test(`activity-tray-${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const activityResponse: ActivityResponse = {
      items: [{ source: "integrity", id: "visual-repertoire", title: "Spanish opening integrity",
        state: "running", phase: "Scanning sources", completed: 2, total: 5,
        updated_at: "2026-09-18T16:00:00Z", error: null, paused: false, promoted: false }],
      counts: { running: 1, queued: 0, paused: 0, failed: 0 }, total: 1, next_offset: null,
      writer: { healthy: true, foreground: 0, background: 0 },
    };
    await prepareVisualUI(page, true, undefined, undefined, activityResponse);
    await page.getByRole("button", { name: /Analysis activity/ }).click();
    await expect(page.getByRole("progressbar", { name: "Spanish opening integrity progress" })).toBeVisible();
    await expect(page.getByText("Phone queue prepared for 2026-09-18.")).toHaveCount(0);
    await page.addStyleTag({ content: "#tempo-activity-content .tempo-activity-item:not(:first-of-type), #tempo-activity-content .tempo-activity-list h3:not(:first-child) { display: none; }" });
    await expect(page.locator(".notification-count")).toHaveCount(0);
    await expect(page).toHaveScreenshot(`activity-tray-${viewport.width}.png`, { animations: "disabled", fullPage: true });
  });
}
test("import-dialog-phone", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page);
  await navigate(page, "Repertoire");
  await page.getByRole("button", { name: /Import PGN/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page).toHaveScreenshot("import-dialog-phone.png");
});

test("board-unavailable", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/queue/window?**", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Local database unavailable" },
    }),
  );
  await page.reload();
  await expect(page.locator(".persistent-board-shell")).toHaveAttribute(
    "data-unavailable",
    "true",
  );
  await expect(
    page.getByRole("button", { name: "Retry", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveScreenshot("board-unavailable.png");
});
test("training-feedback-phone", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page);
  await expect(
    page.getByText("Spanish opening", { exact: true }).first(),
  ).toBeVisible();
  const surface = (await page.locator(".cg-wrap").boundingBox())!;
  for (const rank of [6, 4])
    await page.mouse.click(
      surface.x + (3.5 * surface.width) / 8,
      surface.y + ((rank + 0.5) * surface.height) / 8,
    );
  await expect(
    page.getByText("Try that position again", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".feedback.wrong")).toBeVisible();
  await page.evaluate(() => new Promise<void>((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
  ));
  await expect(page).toHaveScreenshot("training-feedback-phone.png", {
    fullPage: true,
  });
});

for (const viewport of [{ width: 390, height: 844 }, { width: 1280, height: 720 }]) {
  test(`Capture tactic dialog ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport); await prepareVisualUI(page); await navigate(page, "Tactics");
    await page.getByRole("button", { name: "Capture tactic", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page).toHaveScreenshot(`capture-tactic-${viewport.width}.png`, { animations: "disabled", fullPage: true });
  });
}
