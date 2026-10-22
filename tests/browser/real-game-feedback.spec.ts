import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const priorityReason = "Priority review · missed in a recent game";

test("previously studied game-miss priority card starts without a teaching arrow", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: { cards: [{
    id: "previously-studied-miss", queue_entry_id: 2, start_fen: startFen,
    moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
    repertoire_name: "Spanish opening", repertoire_source: "PGN",
    first_correct_at: null, has_study_review: 1, trained_color: "white",
    gameplay_priority_reason: priorityReason,
  }] } }));
  await page.reload();
  await expect(page.getByText(priorityReason)).toBeVisible();
  await expect(page.locator(".cg-wrap svg.cg-shapes > g > g[cgHash]")).toHaveCount(0);
});

test("connected completion fetches a new real-game obligation before cached ordinary work", async ({ page }) => {
  await prepareVisualUI(page);
  let saved = false;
  let releaseReview: (() => void) | undefined;
  const card = (id: string, entry: number, title: string) => ({
    id, queue_entry_id: entry, revision: 1, start_fen: startFen, moves: ["e2e4"],
    content_type: "opening", repertoire_name: title, repertoire_source: "PGN", trained_color: "white",
  });
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: {
    count: 2, cards: saved ? [
      { ...card("missed-decision", 303, "Missed decision"), gameplay_priority_reason: priorityReason },
      card("ordinary-next", 302, "Ordinary next"),
    ] : [card("retained-active", 301, "Active attempt"), card("ordinary-next", 302, "Ordinary next")],
  } }));
  await page.route("**/api/cards/retained-active/review", async route => {
    await new Promise<void>(resolve => { releaseReview = resolve; });
    saved = true;
    await route.fulfill({ json: { persisted: true } });
  });
  await page.reload();
  await expect(page.getByText("Active attempt", { exact: true })).toBeVisible();
  const board = page.locator(".board-frame");
  const fen = await board.getAttribute("data-fen");
  await page.getByRole("button", { name: "Correct", exact: true }).click();
  await expect.poll(() => Boolean(releaseReview)).toBe(true);
  await expect(page.getByText("Ordinary next", { exact: true })).toHaveCount(0);
  await expect(board).toHaveAttribute("data-fen", fen!);
  releaseReview?.();
  await expect(page.getByText("Missed decision", { exact: true })).toBeVisible();
  await expect(page.getByText(priorityReason)).toBeVisible();
  await expect(page.getByText("Ordinary next", { exact: true })).toHaveCount(0);
});
