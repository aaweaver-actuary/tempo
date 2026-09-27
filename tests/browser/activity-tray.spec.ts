import { test, expect, prepareUI, navigate, noPageOverflow } from "./ui-fixtures";

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
