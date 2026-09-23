import { test, expect } from "./observability";
import { navigate } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const priorityReason = "Priority review · missed in a recent game";
const finding = { id: "visual-miss", game_id: "visual-game", ply: 0,
  kind: "repertoire lapse", confidence: 1, card_id: "visual-card" };

test("Games prioritizes a canonical miss and explains the targeted study card", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/game-findings?**", route => route.fulfill({ json: { findings: [finding] } }));
  let submittedDecision: unknown = null;
  let prioritized = false;
  await page.route("**/api/queue/today", route => route.fulfill({ json: {
    cards: [{ id: "visual-card", queue_entry_id: 1, start_fen: startFen,
      moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
      repertoire_name: "Spanish opening", repertoire_source: "PGN",
      first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white",
      gameplay_priority_reason: prioritized ? priorityReason : null }],
  } }));
  await page.route("**/api/game-findings/visual-miss/decision", route => {
    submittedDecision = route.request().postDataJSON();
    prioritized = true;
    return route.fulfill({ json: { id: finding.id, status: "accepted", scheduling: null, queued: true } });
  });
  await navigate(page, "Games");
  await page.getByRole("tab", { name: "Findings" }).click();
  const prioritize = page.getByRole("button", { name: "Prioritize review" });
  await expect(prioritize).toBeVisible();
  await expect(page.getByRole("button", { name: "Count as lapse" })).toHaveCount(0);
  await prioritize.click();
  await expect.poll(() => submittedDecision).toEqual({ decision: "accepted" });

  await navigate(page, "Train");
  await expect(page.getByText(priorityReason)).toBeVisible();
});

test("Games shows the reanalysis instruction when a canonical miss is missing", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/game-findings?**", route => route.fulfill({ json: { findings: [finding] } }));
  await page.route("**/api/game-findings/visual-miss/decision", route => route.fulfill({
    status: 409, json: { detail: "Canonical game decision is unavailable. Reanalyze this game and try again." },
  }));
  await navigate(page, "Games");
  await page.getByRole("tab", { name: "Findings" }).click();
  await page.getByRole("button", { name: "Prioritize review" }).click();
  await expect(page.getByRole("alert")).toContainText("Reanalyze this game");
});
