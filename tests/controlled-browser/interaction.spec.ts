import { test, expect, prepareControlledUI } from "./fixtures";
import { navigate } from "../browser/ui-fixtures";
// Ordinary intercepted-network cases block workers; offline coverage is separate.
test.use({ serviceWorkers: "block" });

test("tablet menu Escape restores the visible navigation trigger", async ({page}) => {
  await page.setViewportSize({width:768,height:1024}); await prepareControlledUI(page);
  const menu=page.locator(".tablet-navigation"); await menu.click();
  await page.locator("#workspace-menu").getByRole("button",{name:"Settings",exact:true}).focus();
  await page.keyboard.press("Escape");
  await expect(menu).toBeFocused();
  await expect(page.locator("#workspace-menu")).toHaveCount(0);
});

test("capture modal keeps incomplete setup draggable and restores focus across browser engines", async ({ page }) => {
  await prepareControlledUI(page); await navigate(page, "Tactics");
  const launch = page.getByRole("button", { name: "Capture tactic", exact: true });
  await launch.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Place R", exact: true }).click();
  const board = dialog.locator(".board-frame");
  await board.scrollIntoViewIfNeeded();
  const surface = await board.locator(".cg-wrap").boundingBox();
  if (!surface) throw new Error("Capture board has no surface");
  const x = surface.x + surface.width / 16;
  await page.mouse.click(x, surface.y + 15 * surface.height / 16);
  await dialog.getByRole("button", { name: "Move pieces", exact: true }).click();
  await board.scrollIntoViewIfNeeded();
  await page.mouse.move(x, surface.y + 15 * surface.height / 16); await page.mouse.down();
  await page.mouse.move(x, surface.y + 9 * surface.height / 16, { steps: 8 }); await page.mouse.up();
  await expect(board).toHaveAttribute("data-fen", "8/8/8/8/R7/8/8/8 w - - 0 1");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0); await expect(launch).toBeFocused();
});
