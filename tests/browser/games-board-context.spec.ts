import { test, expect } from "./observability";
import { Chess } from "chess.js";
import { prepareVisualUI } from "./visual-fixtures";
import { navigate } from "./ui-fixtures";

const startFen = new Chess().fen();
const tacticalFen = "7k/6p1/5K2/8/3Q4/8/8/8 w - - 0 1";

test("Games selection and move navigation keep the board on the selected game", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/game-findings/tactical-queue", route => route.fulfill({ json: {
    item: {
      id: "unrelated-tactic", game_id: "other-game", ply: 12, motif: "fork",
      confidence: 1, played_at: "2026-09-18T12:00:00Z", color: "white",
      opportunity_value_cp: 150, evaluation_loss_cp: 80, accepted_moves: ["d4d8"],
      evidence: { fen: tacticalFen, actual_move_uci: "d4c4" },
    }, remaining: 1,
  } }));
  await navigate(page, "Games");
  const board = page.locator(".board-frame");
  await expect(board).toHaveAttribute("data-fen", startFen);
  const repertoireArrows = page.locator(".cg-wrap svg.cg-shapes > g > g[cgHash]");
  await expect.poll(() => repertoireArrows.count()).toBeGreaterThan(0);
  await page.getByRole("tab", { name: "Findings" }).click();
  await page.getByRole("button", { name: "Review tactic on board" }).click();
  await expect(board).toHaveAttribute("data-fen", tacticalFen);
  await expect(repertoireArrows).toHaveCount(0);
  await page.getByRole("button", { name: "Return to game" }).click();
  await expect(board).toHaveAttribute("data-fen", startFen);
  await expect.poll(() => repertoireArrows.count()).toBeGreaterThan(0);
  await page.getByRole("button", { name: /Forward/ }).click();
  const afterMove = new Chess();
  afterMove.move("e4");
  await expect(board).toHaveAttribute("data-fen", afterMove.fen());
});

test("Games shows loading until the selected game's full moves arrive", async ({ page }) => {
  await prepareVisualUI(page);
  let releaseDetail: (() => void) | undefined;
  await page.route("**/api/games/visual-game", async route => {
    await new Promise<void>(resolve => { releaseDetail = resolve; });
    await route.fallback();
  });
  await navigate(page, "Games");
  await expect(page.locator(".board-unavailable")).toHaveText("Loading selected game…");
  await expect(page.locator(".persistent-board-shell")).toHaveAttribute("data-unavailable", "true");
  releaseDetail?.();
  await expect(page.locator(".board-unavailable")).toHaveCount(0);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", startFen);
});

test("selecting another game changes piece placement and move highlights together", async ({ page }) => {
  await prepareVisualUI(page);
  const summary = (id: string, opening: string) => ({
    id, provider: "lichess", played_at: "2026-09-18T12:00:00Z", speed: "rapid",
    color: "white", result: "1-0", opening_name: opening, analysis_state: "complete",
    major_mistake_ply: null, missed_punishment_ply: null, repertoire_id: null,
    classification: "covered", divergence_ply: null, matched_player_decisions: 0,
    repertoire_opportunities: 0, adherence: null,
  });
  await page.route("**/api/games/summary?**", route => route.fulfill({ json: {
    total: 2, next_cursor: null, aggregates: { page_count: 2 },
    games: [summary("visual-game", "Spanish opening"), summary("second-game", "Sicilian defense")],
  } }));
  await page.route("**/api/games/second-game", route => route.fulfill({ json: {
    id: "second-game", provider: "lichess", username: "player",
    played_at: "2026-09-18T12:00:00Z", speed: "rapid", rated: true,
    color: "white", result: "1-0", start_fen: startFen,
    moves: ["e2e4", "c7c5"], opening_name: "Sicilian defense",
  } }));
  await navigate(page, "Games");
  await page.getByRole("tab", { name: "Library" }).click();
  await page.getByRole("button", { name: /Sicilian defense/ }).click();
  const expected = new Chess();
  expected.move("e4");
  expected.move("c5");
  await page.getByRole("tab", { name: "Review" }).click();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", startFen);
  await page.getByRole("button", { name: "1.e4" }).click();
  await page.getByRole("button", { name: "c5" }).click();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", expected.fen());
  await expect(page.locator(".game-moves button.shown")).toHaveCount(2);
});
