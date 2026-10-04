import { test, expect, navigate, noPageOverflow } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
import { move } from "./product-fixtures";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
function openingCard(id: string, title: string, firstMove: string, queueEntry: number) {
  return { id, queue_entry_id: queueEntry, start_fen: startFen, moves: [firstMove],
    content_type: "opening" as const, repertoire_name: title, repertoire_source: "White.pgn",
    first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" as const };
}

for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 767, height: 844 }]) {
  test(`Phone opening study shows repertoire identity above the board ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await prepareVisualUI(page, true, [openingCard("london", "London System", "d2d4", 1)]);
    const heading = page.locator(".shared-board-heading");
    await expect(heading.getByRole("heading", { name: "London System" })).toBeInViewport();
    await expect(heading.getByText("white to play", { exact: true })).toBeVisible();
    const board = page.locator(".persistent-board-shell .board-viewport");
    const [headingBounds, boardBounds] = await Promise.all([heading.boundingBox(), board.boundingBox()]);
    expect(headingBounds!.y + headingBounds!.height).toBeLessThanOrEqual(boardBounds!.y + 1);
    expect(boardBounds!.width).toBeGreaterThanOrEqual(Math.min(viewport.width - 24, 658) - 1);
    await expect(page.getByRole("heading", { name: "London System", exact: true })).toHaveCount(1);
    const toolbar = page.locator(".shared-board-toolbar");
    await expect(toolbar.getByRole("button", { name: "Keyboard shortcuts" })).toBeHidden();
    await expect(toolbar.getByRole("button", { name: "Flip board", exact: true })).toBeVisible();
    await expect(toolbar.getByRole("button", { name: /Show move/ })).toBeVisible();
    await expect(toolbar.locator("summary", { hasText: "More" })).toBeVisible();
    const details = page.locator(".study-details");
    await expect(details).not.toHaveAttribute("open", "");
    await expect(page.locator(".study-source")).toBeHidden();
    const boardFen = await page.locator(".board-frame").getAttribute("data-fen");
    await details.locator("summary").click();
    await expect(page.locator(".study-source")).toBeVisible();
    await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", boardFen!);
    await noPageOverflow(page);
  });
}

test("Phone study heading follows London-to-Ruy-Lopez card transitions", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page, true, [openingCard("london", "London System", "d2d4", 1), openingCard("ruy", "Ruy Lopez", "e2e4", 2)]);
  await expect(page.locator(".phone-study-heading h2")).toHaveText("London System");
  await move(page, "d2", "d4");
  // Completing the one-move line advances automatically after confirmed review.
  await expect(page.locator(".phone-study-heading h2")).toHaveText("Ruy Lopez");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", startFen);
  await move(page, "e2", "e4");
  await expect(page.locator(".feedback.complete")).toBeVisible();
});

test("Phone opening More supports keyboard help, restart, and edit without resetting on resize", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page, true, [openingCard("london", "London System", "d2d4", 1)]);
  const menu = page.locator(".phone-study-actions");
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: "Keyboard shortcuts" }).click();
  await expect(page.getByRole("dialog", { name: /Keyboard shortcuts/i })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu.locator("summary")).toBeFocused();
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: /Restart/ }).click();
  await expect(menu).not.toHaveAttribute("open", "");
  await expect(menu.locator("summary")).toBeFocused();
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: /Edit card/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  const boardInstance = page.locator(".cg-wrap");
  await boardInstance.evaluate(element => element.setAttribute("data-phone-instance", "original"));
  await page.setViewportSize({ width: 768, height: 1024 });
  await expect(page.locator(".phone-study-heading")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "London System", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Keyboard shortcuts" })).toBeVisible();
  await expect(boardInstance).toHaveAttribute("data-phone-instance", "original");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".phone-study-heading h2")).toHaveText("London System");
  await expect(boardInstance).toHaveAttribute("data-phone-instance", "original");
  await navigate(page, "Tactics");
  await expect(page.locator(".phone-study-heading")).toHaveCount(0);
  await expect(page.locator(".shared-board-toolbar").getByRole("button", { name: "Keyboard shortcuts" })).toBeVisible();
});

test("Long phone repertoire names wrap without hiding the board or overflowing", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await prepareVisualUI(page, true, [openingCard("long", "London System — tournament preparation with a very long repertoire name", "d2d4", 1)]);
  await expect(page.locator(".phone-study-heading h2")).toBeVisible();
  await noPageOverflow(page);
});
