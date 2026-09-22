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
