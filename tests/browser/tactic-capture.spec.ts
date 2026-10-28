import { test, expect, api, nav, move } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";
import type { Locator, Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const puzzlePgn = readFileSync("tests/fixtures/chesscom-puzzle-rush.pgn", "utf8");

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
  await dialog.getByRole("button", { name: "Capture", exact: true }).click();
  const body = (await posted).postDataJSON();
  expect(body.moves).toEqual(["f7f8"]);
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("FEN", { exact: true })).toHaveValue("8/8/8/8/8/8/8/8 w - - 0 1");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
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
  await dialog.getByRole("button", { name: "Capture", exact: true }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("FEN", { exact: true })).toHaveValue("8/8/8/8/8/8/8/8 w - - 0 1");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
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
  await dialog.getByRole("button", { name: "Capture", exact: true }).click();
  expect((await posted).postDataJSON()).toMatchObject({ starting_fen: startingFen, moves: ["e7e5", "g1f3", "b8c6"] });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("FEN", { exact: true })).toHaveValue("8/8/8/8/8/8/8/8 w - - 0 1");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(studyBoard).toHaveAttribute("data-fen", originalFen!);
});

test("Chess.com Puzzle Rush PGN reaches ordinary Training through tactic capture", async ({ page, request }) => {
  await prepareUI(page); await nav(page, "Tactics");
  const studyBoard = page.locator(".persistent-board-shell .board-frame");
  await expect(studyBoard).toHaveAttribute("data-input-enabled", "true");
  const originalFen = await studyBoard.getAttribute("data-fen");
  const lichess = JSON.parse(readFileSync("tests/fixtures/lichess-puzzle-api.json", "utf8"));
  await page.route("https://lichess.org/api/puzzle/PSjmf", route => route.fulfill({ json: lichess }));
  await page.getByRole("button", { name: "Capture tactic", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const captureRequests: string[] = [];
  page.on("request", outgoing => {
    if (outgoing.url().endsWith("/api/tactics/captures") && outgoing.method() === "POST") captureRequests.push(outgoing.postData()!);
  });
  await dialog.getByRole("button", { name: "Paste puzzle PGN or Lichess ID" }).click();
  const input = dialog.getByLabel("Puzzle Rush PGN or Lichess puzzle ID", { exact: true });
  for (const source of [puzzlePgn, "PSjmf", puzzlePgn]) {
    await input.fill(source);
    const posted = page.waitForResponse(response => response.url().endsWith("/api/tactics/captures") && response.request().method() === "POST");
    await dialog.getByRole("button", { name: "Capture", exact: true }).click();
    expect((await posted).ok()).toBe(true);
    await expect(dialog).toBeVisible();
    await expect(input).toHaveValue("");
    await expect(input).toBeFocused();
    await expect(dialog.getByLabel("FEN", { exact: true })).toHaveValue("8/8/8/8/8/8/8/8 w - - 0 1");
  }
  const bodies = captureRequests.map(body => JSON.parse(body));
  expect(bodies).toHaveLength(3);
  expect(new Set(bodies.map(body => body.capture_id)).size).toBe(3);
  const startingFen = "r1b2rk1/ppq2p1p/2np1Qp1/2b5/2B1Pp2/1P6/P1PP2PP/R1B1K1NR b KQ - 0 1";
  expect(bodies[0]).toMatchObject({ starting_fen: startingFen,
    moves: ["c5d4", "f6d4", "c6d4"], source_kind: "puzzle_rush", source_ref: "NDA2OTUxNjU4NDc5NTM3NDc3MA==-b2b3",
    source_url: "https://www.chess.com/puzzles/problem/3211014", note: "" });
  expect(bodies[1]).toMatchObject({ starting_fen: lichess.puzzle.fen, moves: lichess.puzzle.solution,
    source_kind: "other", source_ref: "PSjmf", source_url: "https://lichess.org/training/PSjmf" });
  expect(bodies[2]).toEqual({ ...bodies[0], capture_id: bodies[2].capture_id });
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(studyBoard).toHaveAttribute("data-fen", originalFen!);
  await nav(page, "Train");
  await expect(page.getByText("Captured tactic", { exact: true }).first()).toBeVisible();
  const queue = await (await request.get(`${api}/queue/today`)).json();
  expect(queue.cards.filter((card: { start_fen: string }) => card.start_fen === startingFen)).toHaveLength(1);
  expect(queue.cards.some((card: { start_fen: string }) => card.start_fen === lichess.puzzle.fen)).toBe(true);
});
