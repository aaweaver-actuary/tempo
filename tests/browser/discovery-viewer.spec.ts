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
      state: "ready", opportunity_id: "gap", evidence_fingerprint: "gap-revision", starting_fen: decisionFen,
      accepted_moves_uci: [], suggested_move_uci: "g1f3", suggestion_reason: "same move in a comparable repertoire position",
      candidates: [{ move_uci: "g1f3", score: { cp: 20, mate: null },
        loss_cp: 0, similarity: "same move in a comparable repertoire position",
        repertoire_line_count: 3, exact_transposition: false, example_line_id: "line",
        example_line_name: null, preview_moves_uci: ["g1f3"], engine_version: "Stockfish",
        network_version: "NNUE", depth: 14, report_id: "a".repeat(64),
        source_game_id: "coverage:node", source_ply: 2 },
      { move_uci: "d2d4", score: { cp: 10, mate: null }, loss_cp: 10,
        similarity: "same move in a comparable repertoire position",
        repertoire_line_count: 2, exact_transposition: false, example_line_id: "line-2",
        example_line_name: null, preview_moves_uci: ["d2d4"], engine_version: "Stockfish",
        network_version: "NNUE", depth: 14, report_id: "a".repeat(64),
        source_game_id: "coverage:node", source_ply: 2 }],
      engine_lines: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0, depth: 14 },
        { move_uci: "d2d4", score: { cp: 10, mate: null }, loss_cp: 10, depth: 14 }],
    } }));
    await prepareUI(page);
    await page.getByRole("button", { name: "Discoveries" }).click();
    const viewer = page.getByRole("dialog", { name: "Discoveries" });
    await expect(viewer).toBeVisible();
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", decisionFen);
    await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
    await expect(viewer.getByText(/Suggested Nf3/)).toBeVisible();
    const comparison = viewer.getByRole("region", { name: "Discovery move comparison" });
    await expect(comparison).toContainText("3 comparable repertoire lines");
    await expect(comparison).toContainText("2 comparable repertoire lines");
    await expect(comparison).toContainText("10 cp from best");
    await expect(viewer.locator("svg.cg-shapes > g > g[cgHash]")).toHaveCount(2);
    await page.keyboard.press("ArrowLeft");
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", beforeReply.replace(" e3 ", " - "));
    await viewer.getByRole("button", { name: "Next move" }).click();
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", decisionFen);
    await page.keyboard.press("ArrowRight");
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", /5N2/);
    await viewer.getByRole("button", { name: "Previous move" }).click();
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", decisionFen);
    await expect(viewer.locator("svg.cg-shapes > g > g[cgHash]")).toHaveCount(2);
    await viewer.getByRole("button", { name: "Nf3", exact: true }).click();
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

test("Add and train opens the next discovery before the save responds", async ({ page }) => {
  await page.setViewportSize({ width: 1470, height: 836 });
  const discovery = (id: string) => ({ id, repertoire_id: "rep", kind: "missing_response",
    status: "active", fen_key: beforeReply.split(" ").slice(0, 4).join(" "),
    fen: beforeReply, decision_fen: decisionFen, decision_start_fen: startFen,
    decision_route_uci: ["e2e4", "c7c5"], accepted_moves_uci: [], card_id: null,
    opponent_move_uci: "c7c5", trained_color: "white", score: 1,
    evidence: { supporting_games: 4 }, evidence_fingerprint: `${id}-revision`,
    seen_at: "2026-09-24T00:00:00Z", snoozed_until: null, admission_state: null,
    admitted_card_id: null, unread: false, source_games: [], routes: ["e4 c5"],
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" });
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: {
    discoveries: [discovery("first"), discovery("second")], total: 2,
    next_offset: null, unread_count: 0,
  } }));
  await page.route("**/api/discoveries/*/recommendations", route => {
    const id = route.request().url().includes("first") ? "first" : "second";
    return route.fulfill({ json: {
      state: "ready", opportunity_id: id, evidence_fingerprint: `${id}-revision`,
      starting_fen: decisionFen, accepted_moves_uci: [], suggested_move_uci: "g1f3",
      suggestion_reason: "no supported similarity",
      candidates: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0,
        similarity: "no supported similarity", example_line_id: null, example_line_name: null,
        repertoire_line_count: 0, exact_transposition: false,
        preview_moves_uci: ["g1f3"], engine_version: "Stockfish", network_version: "NNUE",
        depth: 14, report_id: "a".repeat(64), source_game_id: "coverage:node", source_ply: 2 }],
      engine_lines: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0, depth: 14 }],
    } });
  });
  let finishSave: (() => void) | undefined;
  await page.route("**/api/discoveries/first/accept", route => new Promise<void>((resolve) => {
    finishSave = () => { void route.fulfill({ status: 202, json: {
      status: "preparing", intent_id: "intent",
    } }).then(resolve); };
  }));
  await page.route("**/api/discovery-admissions/intent", route => route.fulfill({ json: {
    state: "queued", error: null,
  } }));
  await prepareUI(page);
  await page.getByRole("button", { name: "Discoveries" }).click();
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("1 of 2 · white to move")).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Add and train" })).toBeEnabled();
  await viewer.getByRole("button", { name: "Add and train" }).click();
  await expect(viewer.getByText("2 of 2 · white to move")).toBeVisible();
  await expect.poll(() => Boolean(finishSave)).toBe(true);
  finishSave?.();
  await viewer.getByRole("button", { name: "Previous", exact: true }).click();
  await expect(viewer.getByRole("button", { name: "Add and train" })).toBeDisabled();
});
