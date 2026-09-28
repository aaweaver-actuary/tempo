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
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: {
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

test("Games loads every page of pending findings for the selected game", async ({ page }) => {
  await prepareVisualUI(page);
  const requestedOffsets: string[] = [];
  await page.route("**/api/game-findings?**", route => {
    const offset = new URL(route.request().url()).searchParams.get("offset") ?? "0";
    requestedOffsets.push(offset);
    return route.fulfill({ json: offset === "0"
      ? { findings: [finding], next_offset: 1, total: 2 }
      : { findings: [{ ...finding, id: "second-finding", kind: "first big mistake" }],
          next_offset: null, total: 2 } });
  });
  await navigate(page, "Games");
  await page.getByRole("tab", { name: "Findings" }).click();
  await expect(page.getByText("repertoire lapse", { exact: true })).toBeVisible();
  await expect(page.getByText("first big mistake", { exact: true })).toBeVisible();
  expect(requestedOffsets).toEqual(expect.arrayContaining(["0", "1"]));
});

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

test("Games shows reanalysis without repeatedly fetching its own summary", async ({ page }) => {
  await prepareVisualUI(page);
  let summaryRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/games/summary") summaryRequests++;
  });
  await page.route("**/api/game-findings?**", route => route.fulfill({ json: { findings: [finding] } }));
  await page.route("**/api/game-findings/visual-miss/decision", route => route.fulfill({
    status: 409, json: { detail: "Canonical game decision is unavailable. Reanalyze this game and try again." },
  }));
  await navigate(page, "Games");
  await page.getByRole("tab", { name: "Findings" }).click();
  await page.getByRole("button", { name: "Prioritize review" }).click();
  await expect(page.getByRole("alert")).toContainText("Reanalyze this game");
  await page.waitForTimeout(200);
  expect(summaryRequests).toBeLessThanOrEqual(2);
});
