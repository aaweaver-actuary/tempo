import { test, expect, prepareUI, noPageOverflow } from "./ui-fixtures";

const beforeReply = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
const decisionFen = "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2";
const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

for (const viewport of [{ width: 390, height: 844 }, { width: 1470, height: 836 }]) {
  test(`discovery viewer compares one learner decision and returns from Builder ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route("**/api/discoveries?**", route => route.fulfill({ json: {
      discoveries: [{ id: "gap", repertoire_id: "rep", kind: "missing_response",
        status: "active", fen_key: beforeReply.split(" ").slice(0, 4).join(" "),
        fen: beforeReply, decision_fen: decisionFen, decision_start_fen: startFen,
        decision_route_uci: ["e2e4", "c7c5"],
        accepted_moves_uci: [], card_id: null, opponent_move_uci: "c7c5", trained_color: "white",
        score: 1, evidence: { supporting_games: 4 }, evidence_fingerprint: "gap-revision",
        seen_at: "2026-09-24T00:00:00Z", snoozed_until: null, admission_state: null,
        admitted_card_id: null, unread: false, source_games: [], routes: ["e4 c5"],
        created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" }],
      total: 1, next_offset: null, unread_count: 0,
    } }));
    await page.route("**/api/discoveries/gap/recommendations", route => route.fulfill({ json: {
      state: "ready", opportunity_id: "gap", starting_fen: decisionFen,
      accepted_moves_uci: [], candidates: [{ move_uci: "g1f3", score: { cp: 20, mate: null },
        loss_cp: 0, similarity: "no supported similarity", example_line_id: null,
        example_line_name: null, preview_moves_uci: ["g1f3"], engine_version: "Stockfish",
        network_version: "NNUE", depth: 14, report_id: "a".repeat(64),
        source_game_id: "coverage:node", source_ply: 2 }],
      engine_lines: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0, depth: 14 }],
    } }));
    await prepareUI(page);
    await page.getByRole("button", { name: "Discoveries" }).click();
    const viewer = page.getByRole("dialog", { name: "Discoveries" });
    await expect(viewer).toBeVisible();
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", decisionFen);
    await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
    await viewer.getByRole("button", { name: "Nf3" }).click();
    await expect(viewer.getByText("Nf3 selected.", { exact: false })).toBeVisible();
    await expect(viewer.getByRole("button", { name: "Add and train" })).toBeEnabled();
    await noPageOverflow(page);
    await viewer.getByRole("button", { name: "Open in Builder" }).click();
    await expect(page.getByRole("button", { name: "Return to discovery" })).toBeVisible();
    await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", decisionFen.replace(" c6 ", " - "));
    await expect(page.locator(".analysis-moves")).toContainText("e4");
    await expect(page.locator(".analysis-moves")).toContainText("c5");
    await page.getByRole("button", { name: "Return to discovery" }).click();
    await expect(viewer).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(viewer).toHaveCount(0);
  });
}
