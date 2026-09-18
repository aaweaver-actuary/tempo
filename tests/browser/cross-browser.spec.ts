import { test, expect } from "./observability";
import { navigate, noPageOverflow } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";

test("critical navigation, board input, split and dialogs work across browser engines", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await prepareVisualUI(page);
  await navigate(page, "Builder");
  const board = page.locator(".board-frame");
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  const surface = await page.locator(".cg-wrap").boundingBox();
  if (!surface) throw new Error("Board has no visible surface");
  for (const [file, rank] of [
    [4, 6],
    [4, 4],
  ])
    await page.mouse.click(
      surface.x + ((file + 0.5) * surface.width) / 8,
      surface.y + ((rank + 0.5) * surface.height) / 8,
    );
  await expect(board).toHaveAttribute("data-fen", /4P3/);
  const separator = page.getByRole("separator", { name: "Board size" });
  await separator.focus();
  await page.keyboard.press("ArrowRight");
  await expect(separator).toHaveAttribute("aria-valuenow", "48");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  const note = page.getByRole("textbox", { name: "Position comment" });
  await note.fill("Remember this position.");
  await page.getByRole("tab", { name: "Analysis", exact: true }).click();
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  await expect(note).toHaveValue("Remember this position.");
  await navigate(page, "Repertoire");
  await noPageOverflow(page);
  const importButton = page.getByRole("button", { name: /Import PGN/ });
  await importButton.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(importButton).toBeFocused();
  await navigate(page, "Games");
  await page.getByRole("tab", { name: "Library", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Filter source" }),
  ).toBeVisible();
  await noPageOverflow(page);
});


test("tablet menu Escape restores the visible navigation trigger", async ({page}) => {
  await page.setViewportSize({width:768,height:1024}); await prepareVisualUI(page);
  const menu=page.locator(".tablet-navigation"); await menu.click();
  await page.locator("#workspace-menu").getByRole("button",{name:"Games",exact:true}).focus();
  await page.keyboard.press("Escape");
  await expect(menu).toBeFocused();
  await expect(page.locator("#workspace-menu")).toHaveCount(0);
});
