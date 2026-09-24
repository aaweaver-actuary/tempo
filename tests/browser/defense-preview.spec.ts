import { test, expect, nav, boardVisible } from "./product-fixtures";

const originalFen = "8/k1p2p2/1p2p3/1P2Pn2/PR6/8/3r1PKP/8 w - - 3 36";
const previewFen = "8/k1p2p2/1p2p3/1P2Pn2/P1R5/8/3r1PKP/8 b - - 4 36";

for (const width of [390, 1440]) {
  test(`reported Rc4 defensive preview stays readable and returns to the decision at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 600 ? 844 : 900 });
    await page.route("**/api/queue/today", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ cards: [{
        id: "defense-preview", queue_entry_id: 1001, start_fen: originalFen,
        moves: [], content_type: "defense", repertoire_name: "Defensive tactics",
        repertoire_source: "Your analyzed games", source_ref: "preview-candidate",
        trained_color: "white", revision: 3, attempt_state: "clean",
      }], count: 1, diagnostics: [] }),
    }));
    await page.route("**/api/defense-exercises/preview-candidate", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ candidate_id: "preview-candidate", card_id: "defense-preview",
        exercise_revision: 3, rubric_version: 3, recognition_required: true,
        prompt: "What danger should your next move account for?",
        proposed_move_uci: "b4c4", proposed_move_san: "Rc4",
        preview_fen: previewFen, fork_move_san: "Ne3+" }),
    }));
    await page.route("**/api/defense-exercises/preview-candidate/recognition", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ status: "ready_for_move", recognition_correct: true,
        feedback: { knight_route: [{ from_square: "f5", to_square: "e3" }],
          fork_geometry: { knight_to: "e3", king: { square: "g2" },
            major: { square: "c4", piece: "rook" } }, sound_moves: [],
          refutation_uci: ["b4c4", "f5e3", "g2g3", "e3c4"] } }),
    }));
    await page.goto("/");
    await nav(page, "Train");
    await expect(page.getByText("Preview after 36.Rc4 · Black to play")).toBeVisible();
    await boardVisible(page);
    const previewBounds = await page.locator(".persistent-board-shell").boundingBox();
    const panelBounds = await page.locator(".defense-study-panel").boundingBox();
    expect(previewBounds).not.toBeNull();
    expect(panelBounds).not.toBeNull();
    if (width < 600) expect(previewBounds!.y + previewBounds!.height).toBeLessThanOrEqual(panelBounds!.y + 2);
    else expect(previewBounds!.x + previewBounds!.width).toBeLessThanOrEqual(panelBounds!.x + 2);
    for (const [label, square] of [
      ["Dangerous piece square", "f5"], ["Destination square", "e3"],
      ["Your king square", "g2"], ["Threatened piece square", "c4"],
    ]) {
      await page.getByLabel(label, { exact: true }).fill(square);
      await page.getByRole("button", { name: "Select", exact: true }).click();
    }
    await page.getByLabel("Consequence").selectOption("checking_fork");
    await page.getByRole("button", { name: "Submit assessment" }).click();
    await expect(page.getByText(/After 36.Rc4, Ne3\+ checks the king/)).toBeVisible();
    await page.getByRole("button", { name: "Continue to defense" }).click();
    await expect(page.getByText("white to play")).toBeVisible();
    await expect(page.getByText(/Back at your original turn/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
  });
}
