import { test, expect } from "./observability";
import { navigate } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
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
      if (workspace === "Train")
        await expect(
          page.getByText("Spanish opening", { exact: true }).first(),
        ).toBeVisible();
      if (workspace === "Tactics")
        await expect(page.getByText(/Puzzle \d+ of/)).toBeVisible();
      if (workspace === "Builder")
        await expect(
          page.getByRole("combobox", { name: "Active repertoire" }),
        ).toHaveValue("visual-repertoire");
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
test("service-unavailable", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/progress", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Local database unavailable. Check the data mount." },
    }),
  );
  await navigate(page, "Progress");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveScreenshot("service-unavailable.png");
});
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
  await page.route("**/api/queue/today", (route) =>
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
  await expect(page).toHaveScreenshot("training-feedback-phone.png", {
    fullPage: true,
  });
});
