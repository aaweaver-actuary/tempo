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
    }
});
test("pointer resizing persists and clamps without horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await prepareVisualUI(page);
  await navigate(page, "Train");
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

test('owner changes clear an unfinished square selection at the same position', async ({page}) => {
  await prepareVisualUI(page); await navigate(page,'Train');
  const surface=(await page.locator('.cg-wrap').boundingBox())!;
  await page.mouse.click(surface.x+4.5*surface.width/8,surface.y+6.5*surface.height/8);
  await expect(page.locator('cg-board square.selected').filter({visible:true})).toHaveCount(1);
  await navigate(page,'Tactics');
  await expect(page.locator('cg-board square.selected').filter({visible:true})).toHaveCount(0);
});
