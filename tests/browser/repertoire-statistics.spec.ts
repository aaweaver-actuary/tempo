import { test, expect, navigate } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

for (const width of [390, 1280]) {
  test(`repertoire statistics inspect a reached position and retain context ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 720 });
    await prepareVisualUI(page);
    await page.route("**/api/repertoires/visual-repertoire/statistics**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.endsWith("/positions")) return route.fulfill({ json: {
        positions: [{ fen_key: startFen.split(" ").slice(0, 4).join(" "), fen: startFen,
          games: 3, encounters: 3, correct: 2, missed: 1, last_seen_at: "2026-09-18T12:00:00Z",
          expected_moves: ["e2e4"], played_moves: [{ move_uci: "e2e4", count: 2 }, { move_uci: "d2d4", count: 1 }],
          card_id: "visual-card", sample_game_id: "visual-game", sample_ply: 0 }], total: 1, next_cursor: null,
      } });
      return route.fulfill({ json: {
        repertoire_id: "visual-repertoire", window: "90d", graph_updated_at: "2026-09-18T12:00:00Z",
        graph_state: "ready", game_state: "ready",
        prefix: { total: 3, active: 2, studied: 1, unseen: 2, locked: 1, paused: 0 },
        cards: { total: 8, new: 2, learning: 3, mature: 2, locked: 1, difficult: 1, due_today: 1, due_next_seven_days: 2 },
        study: { correct: 4, attempts: 5, accuracy: 0.8 },
        games: { matched: 3, correct: 2, decisions: 3, adherence: 2 / 3, wins: 2, draws: 0, losses: 1, positions_seen: 1, positions_total: 4 },
        unlocks: [{ card_id: "child", parent_card_id: "parent", line_name: "Spanish continuation", parent_due_date: "2026-09-19", earliest_unlock_date: "2026-09-22", status: "ready" }],
      } });
    });
    await navigate(page, "Repertoire");
    await page.getByRole("button", { name: "Statistics", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Prefix cards" })).toBeVisible();
    await expect(page.getByText("80.0%")).toBeVisible();
    await expect(page.getByText(/Ready for introduction/)).toBeVisible();
    await expect(page.getByText(/within your daily new-card limit/)).toBeVisible();
    await expect(page.locator(".repertoire-position-board .board-frame")).toHaveAttribute("data-fen", startFen);
    const supportingRequest = page.waitForRequest((request) => request.url().includes("/api/games/summary") && request.url().includes("repertoire_id=visual-repertoire"));
    await page.getByRole("button", { name: "View supporting games" }).click();
    await supportingRequest;
    await navigate(page, "Repertoire");
    await expect(page.getByRole("heading", { name: "Prefix cards" })).toBeVisible();
    await page.getByRole("button", { name: "← Repertoires" }).click();
    await expect(page.getByRole("button", { name: "Statistics", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  });
}
