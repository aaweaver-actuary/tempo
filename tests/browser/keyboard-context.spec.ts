import { Chess } from "chess.js";
import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";
import { navigate } from "./ui-fixtures";
import { expectedPieces, renderedPieces, playMove, squareCenter } from "./keyboard-fixtures";

test.use({ serviceWorkers: "block" });

test("training arrows never uncover the next answer and R restores the decision without grading", async ({ page }) => {
  await prepareVisualUI(page);
  const writes: string[] = [];
  page.on("request", request => { if (request.method() === "POST" && /\/review$/.test(request.url())) writes.push(request.url()); });
  const board = page.locator(".board-frame");
  const root = new Chess().fen();
  await expect(board).toHaveAttribute("data-fen", root);
  await playMove(page, board, "e2", "e4");
  const decision = new Chess(); decision.move("e4"); decision.move("e5");
  await expect(board).toHaveAttribute("data-fen", decision.fen());
  await page.keyboard.press("ArrowRight"); await page.keyboard.press("End");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(decision.fen()));
  await page.keyboard.press("Home");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(root));
  await expect(board).toHaveAttribute("data-input-enabled", "false");
  await page.keyboard.press("f"); await expect(board).toHaveAttribute("data-orientation", "black");
  await page.keyboard.press("r");
  await expect(board).toHaveAttribute("data-orientation", "white");
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(decision.fen()));
  expect(writes).toEqual([]);
});

test("Builder shortcuts cancel a held piece and preserve notes, selection and splitter keys", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Builder");
  const board = page.locator(".board-frame");
  const root = new Chess().fen();
  await board.scrollIntoViewIfNeeded();
  const from = await squareCenter(board, "e2"), to = await squareCenter(board, "e4");
  await page.mouse.move(from.x, from.y); await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 8 });
  await expect(board.locator("piece.dragging")).toHaveCount(1);
  await page.keyboard.press("r");
  await expect(board.locator("piece.dragging")).toHaveCount(0);
  await page.mouse.up();
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(root));
  await playMove(page, board, "e2", "e4");
  const decision = new Chess(); decision.move("e4");
  await expect(board).toHaveAttribute("data-fen", decision.fen());
  await page.keyboard.press("ArrowUp"); await page.keyboard.press("r");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(decision.fen()));
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  const note = page.getByRole("textbox", { name: "Position comment" });
  await note.fill("Keep this draft"); await note.press("f"); await note.press("ArrowLeft");
  await expect(board).toHaveAttribute("data-orientation", "white");
  await expect(board).toHaveAttribute("data-fen", decision.fen());
  const splitter = page.getByRole("separator", { name: "Board size" });
  await splitter.focus(); await splitter.press("ArrowRight");
  await expect(board).toHaveAttribute("data-fen", decision.fen());
});

test("Settings letter preference takes effect immediately and persists while arrows and help work", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Settings");
  await page.getByRole("tab", { name: "Board", exact: true }).click();
  const preference = page.getByRole("checkbox", { name: /Letter keyboard shortcuts/ });
  await preference.uncheck();
  await navigate(page, "Builder");
  const board = page.locator(".board-frame");
  await playMove(page, board, "e2", "e4");
  await page.keyboard.press("f"); await expect(board).toHaveAttribute("data-orientation", "white");
  await page.keyboard.press("Home"); await expect(board).toHaveAttribute("data-fen", new Chess().fen());
  await page.locator(".board-viewport").focus(); await page.keyboard.press("?");
  const help = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(help).toBeVisible(); await expect(help).toContainText("Flip board (unavailable)");
  await page.keyboard.press("Escape"); await expect(help).toHaveCount(0);
  await page.reload(); await navigate(page, "Settings");
  await page.getByRole("tab", { name: "Board", exact: true }).click(); await expect(preference).not.toBeChecked();
  await preference.check(); await navigate(page, "Builder");
  await page.keyboard.press("f"); await expect(page.locator(".board-frame")).toHaveAttribute("data-orientation", "black");
});

test("Escape closes Builder search and header popups one at a time and restores their openers", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Builder");
  await page.getByRole("tab", { name: "Repertoire", exact: true }).click();
  const search = page.getByRole("button", { name: /Position search/ });
  await search.click(); await expect(page.getByRole("dialog", { name: "Position search" })).toBeVisible();
  await page.keyboard.press("Escape"); await expect(search).toBeFocused();
  const local = page.getByRole("button", { name: "Open local data menu" });
  const notifications = page.getByRole("button", { name: "Notifications" });
  await local.click(); await notifications.click();
  await page.keyboard.press("Escape");
  await expect(notifications).toBeFocused(); await expect(page.locator("#local-data-menu")).toBeVisible();
  await page.keyboard.press("Escape"); await expect(page.locator("#local-data-menu")).toHaveCount(0);
  await expect(local).toBeFocused();
  const activity = page.getByRole("button", { name: "Analysis activity" });
  await activity.click(); await page.keyboard.press("Escape"); await expect(activity).toBeFocused();
  await expect(page.locator("#tempo-activity-content")).toHaveCount(0);
});
