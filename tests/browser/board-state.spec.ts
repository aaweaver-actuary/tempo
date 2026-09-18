import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";
import { navigate, boardWorkspaces } from "./ui-fixtures";
test("every directed workspace transition isolates annotations and board input", async ({
  page,
}) => {
  await prepareVisualUI(page);
  for (const source of boardWorkspaces)
    for (const destination of boardWorkspaces.filter(
      (name) => name !== source,
    )) {
      await navigate(page, source);
      await navigate(page, destination);
      await expect(page.locator(".persistent-board-shell")).toHaveAttribute(
        "data-board-owner",
        destination.toLowerCase(),
      );
      if (destination === "Games")
        await expect(page.locator(".board-frame")).toHaveAttribute(
          "data-input-enabled",
          "false",
        );
      if (destination === "Builder")
        await expect(page.locator(".board-frame")).toHaveAttribute(
          "data-input-enabled",
          "true",
        );
    }
});
test("builder annotations never appear in games", async ({ page }) => {
  await prepareVisualUI(page);
  await navigate(page, "Builder");
  await expect(
    page.getByRole("textbox", { name: "Position comment" }),
  ).toBeEnabled();
  const board = page.locator(".cg-wrap");
  const bounds = (await board.boundingBox())!;
  await page.mouse.move(
    bounds.x + (4.5 * bounds.width) / 8,
    bounds.y + (4.5 * bounds.height) / 8,
  );
  await page.mouse.down({ button: "right" });
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
  );
  await page.mouse.up({ button: "right" });
  await expect(board.locator("svg.cg-shapes circle")).toHaveCount(1);
  await navigate(page, "Games");
  await expect(board.locator("svg.cg-shapes circle")).toHaveCount(0);
});
test("pointer resizing persists and clamps without horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await prepareVisualUI(page);
  await navigate(page, "Builder");
  const divider = page.getByRole("separator", { name: "Board size" });
  const before = (await divider.boundingBox())!;
  await page.mouse.move(before.x + 10, before.y + 80);
  await page.mouse.down();
  await page.mouse.move(before.x + 130, before.y + 80);
  await page.mouse.up();
  expect(Number(await divider.getAttribute("aria-valuenow"))).toBeGreaterThan(
    46,
  );
  const preference = await divider.getAttribute("aria-valuenow");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(divider).toHaveAttribute("aria-valuenow", preference!);
  await page.getByRole("button", { name: "Reset board size" }).click();
  await expect(divider).toHaveAttribute("aria-valuenow", "46");
});

test("shared toolbar flip persists while stepping through a game", async ({
  page,
}) => {
  await prepareVisualUI(page);
  await navigate(page, "Games");
  await expect(
    page.getByRole("heading", { name: "Spanish opening", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Flip board", exact: true }).click();
  await expect(page.locator(".board-frame")).toHaveAttribute(
    "data-orientation",
    "black",
  );
  await page
    .locator(".shared-board-toolbar")
    .getByRole("button", { name: /Forward/ })
    .click();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3/);
  await expect(page.locator(".board-frame")).toHaveAttribute(
    "data-orientation",
    "black",
  );
});

test('owner changes clear an unfinished square selection at the same position', async ({page}) => {
  await prepareVisualUI(page); await navigate(page,'Builder');
  await expect(page.getByRole('textbox',{name:'Position comment'})).toBeEnabled();
  const surface=(await page.locator('.cg-wrap').boundingBox())!;
  await page.mouse.click(surface.x+4.5*surface.width/8,surface.y+6.5*surface.height/8);
  await expect(page.locator('cg-board square.selected').filter({visible:true})).toHaveCount(1);
  await navigate(page,'Train');
  await expect(page.locator('cg-board square.selected').filter({visible:true})).toHaveCount(0);
});
