import { expect, type Page } from "@playwright/test";
import { Chess } from "chess.js";
import { squareCenter, renderedPieces, expectedPieces } from "./keyboard-fixtures";

export async function preparePromotionStudy(page: Page, black = false) {
  const now = new Date();
  const localDate = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const fen = black ? "7k/8/8/8/8/8/p7/7K b - - 0 1" : "7k/P7/8/8/8/8/8/7K w - - 0 1";
  const move = black ? "a2a1n" : "a7a8n";
  const card = { id: "promotion-card", queue_entry_id: 991, cycle: 0, latest_review_id: 0, revision: 1,
    start_fen: fen, moves: [], content_type: "study_exercise", kind: "exercise", repertoire_id: null,
    repertoire_name: "Original promotion study", repertoire_source: "Study", study_id: "promotion-study", study_exercise_id: "promotion-exercise",
    study_snapshot: { schema_version: 1, grader_version: 1, exercise_id: "promotion-exercise", revision: 1, fen,
      specification: { type: "move_line", prompt: "Promote to a knight", hint: "", explanation: "The original reference promotes to a knight.",
        further_analysis: "", mode: "single", grading_policy: "open_judgment", accepted_lines: [[move]] } } };
  const queue = { local_date: localDate, count: 1, cards: [card] };
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: queue }));
  await page.route("**/api/queue/prepared?**", (route) => route.fulfill({ json: { ...queue, prepared_at: now.toISOString(),
    projection: { state: "ready", generation: 1, updated_at: null, refresh_pending: 0, last_error: null, blocked_count: 0 } } }));
  await page.goto("/");
  await expect(page.getByText("Promote to a knight")).toBeVisible();
  const board = page.locator(".board-frame").first();
  await expect(board).toHaveAttribute("data-fen", fen);
  await expect(board.locator("piece.anim")).toHaveCount(0);
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(fen));
  return { localDate, fen, move };
}

export async function dragStudyKnightPromotion(page: Page, fen: string, move: string) {
  if (move.startsWith("a2")) {
    await page.getByRole("button", { name: "Flip board", exact: true }).click();
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-orientation", "black");
  }
  await page.getByRole("combobox", { name: "Promotion", exact: true }).selectOption("n");
  const board = page.locator(".board-frame").first();
  await board.scrollIntoViewIfNeeded();
  const origin = await squareCenter(board, move.slice(0, 2));
  const destination = await squareCenter(board, move.slice(2, 4));
  await page.mouse.move(origin.x, origin.y);
  await page.mouse.down();
  await page.mouse.move(destination.x, destination.y, { steps: 8 });
  await page.mouse.up();
  const expected = new Chess(fen);
  expected.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: "n" });
  await expect(board).toHaveAttribute("data-fen", expected.fen());
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(expected.fen()));
  await expect(page.getByText(`Answer: ${move}`, { exact: true })).toBeVisible();
}
