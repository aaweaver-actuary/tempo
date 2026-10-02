import { test, expect, api, nav, move } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";
import type { Locator, Page } from "@playwright/test";

async function point(board: Locator, square: string) {
  await board.scrollIntoViewIfNeeded();
  const bounds = await board.locator(".cg-wrap").boundingBox();
  if (!bounds) throw new Error("Capture board has no surface");
  const blackAtBottom = await board.getAttribute("data-orientation") === "black";
  const fileIndex = square.charCodeAt(0) - 97;
  const rankIndex = Number(square[1]) - 1;
  return { x: bounds.x + ((blackAtBottom ? 7 - fileIndex : fileIndex) + .5) * bounds.width / 8,
    y: bounds.y + ((blackAtBottom ? rankIndex : 7 - rankIndex) + .5) * bounds.height / 8 };
}
async function click(page: Page, board: Locator, square: string) {
  const location = await point(board, square); await page.mouse.click(location.x, location.y);
}
async function play(page: Page, board: Locator, from: string, to: string) {
  await click(page, board, from); await click(page, board, to);
}

test("FEN capture records a real-board solution and is reviewed through ordinary Training", async ({ page, request }) => {
  await prepareUI(page); await nav(page, "Tactics");
  await expect(page.getByText(/Puzzle \d+ of/)).toBeVisible();
  const solveFen = await page.locator(".persistent-board-shell .board-frame").getAttribute("data-fen");
  await page.getByRole("button", { name: "Capture tactic", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const board = dialog.locator(".board-frame");
  await dialog.getByLabel("FEN", { exact: true }).fill("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1");
  await dialog.getByRole("button", { name: "Solution", exact: true }).click();
  await play(page, board, "f7", "f8");
  await expect(dialog.locator(".solution-line")).toContainText("Qf8#");
  await expect(board.locator("piece.white.queen:not(.ghost)")).toHaveCount(1);
  const posted = page.waitForRequest(request => request.url().endsWith("/api/tactics/captures") && request.method() === "POST");
  await dialog.getByRole("button", { name: "Add to training" }).click();
  const body = (await posted).postDataJSON();
  expect(body.moves).toEqual(["f7f8"]);
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".persistent-board-shell .board-frame")).toHaveAttribute("data-fen", solveFen!);
  await nav(page, "Train");
  await expect(page.getByText("Captured tactic", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Original" })).toHaveCount(0);
  const queue = await (await request.get(`${api}/queue/today`)).json();
  const captured = queue.cards.find((card: { start_fen: string }) => card.start_fen === body.starting_fen);
  expect(captured?.attempt_state).toBe("guided");
  const reviewed = page.waitForResponse(response => response.url().includes(`/api/cards/${captured.id}/review`) && response.request().method() === "POST");
  await move(page, "f7", "f8");
  expect((await reviewed).ok()).toBe(true);
});

test("manual capture places and removes pieces and freely drags an incomplete setup on the real board", async ({ page }) => {
  await prepareUI(page); await nav(page, "Tactics");
  await page.getByRole("button", { name: "Capture tactic", exact: true }).click();
  const dialog = page.getByRole("dialog"), board = dialog.locator(".board-frame");
  await expect(dialog.getByRole("button", { name: "Solution", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "Place R", exact: true }).click();
  await click(page, board, "a1");
  await dialog.getByRole("button", { name: "Move pieces", exact: true }).click();
  const start = await point(board, "a1"), end = await point(board, "a4");
  await page.mouse.move(start.x, start.y); await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 8 }); await page.mouse.up();
  await expect(board).toHaveAttribute("data-fen", "8/8/8/8/R7/8/8/8 w - - 0 1");
  await dialog.getByRole("button", { name: "Place K", exact: true }).click(); await click(page, board, "h1");
  await dialog.getByRole("button", { name: "Place k", exact: true }).click(); await click(page, board, "h8");
  await dialog.getByRole("button", { name: "Place P", exact: true }).click(); await click(page, board, "b2");
  await dialog.getByRole("button", { name: "Remove piece", exact: true }).click(); await click(page, board, "b2");
  await expect(board.locator("piece:not(.ghost)")).toHaveCount(3);
  await dialog.getByRole("button", { name: "Solution", exact: true }).click();
  await play(page, board, "a4", "a8");
  await expect(dialog.locator(".solution-line")).toContainText("Ra8+");
  await dialog.getByRole("button", { name: "Add to training" }).click();
  await expect(dialog).toHaveCount(0);
});

test("Black-first capture keeps its orientation while typed SAN and real-board moves save one solution", async ({ page }) => {
  await prepareUI(page); await nav(page, "Tactics");
  await expect(page.getByText(/Puzzle \d+ of/)).toBeVisible();
  const studyBoard = page.locator(".persistent-board-shell .board-frame");
  await expect(studyBoard).toHaveAttribute("data-input-enabled", "true");
  const originalFen = await studyBoard.getAttribute("data-fen");
  await page.getByRole("button", { name: "Capture tactic", exact: true }).click();
  const dialog = page.getByRole("dialog"), board = dialog.locator(".board-frame");
  const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 12";
  await dialog.getByLabel("FEN", { exact: true }).fill(startingFen);
  await expect(board).toHaveAttribute("data-orientation", "black");
  await dialog.getByRole("button", { name: "Solution", exact: true }).click();
  const sanInput = dialog.getByLabel("SAN moves", { exact: true });
  await sanInput.fill("12...e5 13.Nf3 invalid");
  await sanInput.press("Enter");
  await expect(dialog.getByRole("alert")).toContainText("invalid");
  await expect(board).toHaveAttribute("data-fen", startingFen);
  await expect(sanInput).toHaveValue("12...e5 13.Nf3 invalid");
  await sanInput.fill("12...e5 13.Nf3");
  await sanInput.press("Enter");
  await expect(sanInput).toHaveValue("");
  await play(page, board, "b8", "c6");
  await expect(board).toHaveAttribute("data-orientation", "black");
  await expect(board).toHaveAttribute("data-fen", "r1bqkbnr/pppp1ppp/2n5/4p3/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 14");
  const posted = page.waitForRequest(request => request.url().endsWith("/api/tactics/captures") && request.method() === "POST");
  await dialog.getByRole("button", { name: "Add to training" }).click();
  expect((await posted).postDataJSON()).toMatchObject({ starting_fen: startingFen, moves: ["e7e5", "g1f3", "b8c6"] });
  await expect(dialog).toHaveCount(0);
  await expect(studyBoard).toHaveAttribute("data-fen", originalFen!);
});
