import { expect, test } from "@playwright/test";
import { prepareVisualUI } from "./visual-fixtures";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

test("discovery preview backlog leaves a prompt foreground training queue refresh", async ({ page }) => {
  await prepareVisualUI(page);
  const discoveries = Array.from({ length: 6 }, (_, index) => ({
    id: `preview-${index}`, repertoire_id: "visual-repertoire", kind: "missing_response",
    status: "active", fen_key: startFen.split(" ").slice(0, 4).join(" "),
    fen: startFen, decision_fen: startFen, decision_start_fen: startFen,
    decision_route_uci: [], accepted_moves_uci: [], card_id: null,
    opponent_move_uci: null, trained_color: "white", score: 1,
    evidence: { supporting_games: 4 }, evidence_fingerprint: `revision-${index}`,
    seen_at: "2026-09-18T00:00:00Z", snoozed_until: null, admission_state: null,
    admitted_card_id: null, unread: false, source_games: [], routes: [],
    created_at: "2026-09-18T00:00:00Z", updated_at: "2026-09-18T00:00:00Z",
  }));
  await page.route("**/api/discoveries?**", (route) => route.fulfill({ json: {
    discoveries, total: discoveries.length, next_offset: null, unread_count: 0,
  } }));

  let activePreviews = 0;
  let maximumActivePreviews = 0;
  let startedPreviews = 0;
  const previewWorkClasses: string[] = [];
  let releasePreviews: (() => void) | undefined;
  const previewHold = new Promise<void>((resolve) => { releasePreviews = resolve; });
  await page.route("**/api/discoveries/*/recommendations", async (route) => {
    startedPreviews += 1;
    activePreviews += 1;
    maximumActivePreviews = Math.max(maximumActivePreviews, activePreviews);
    previewWorkClasses.push(route.request().headers()["x-tempo-work-class"] ?? "");
    try {
      await previewHold;
      const discovery = discoveries.find(item => route.request().url().includes(`/${item.id}/`))!;
      await route.fulfill({ json: { state: "ready", opportunity_id: discovery.id,
        evidence_fingerprint: discovery.evidence_fingerprint, starting_fen: startFen,
        candidates: [{ move_uci: "e2e4", score: { cp: 20, mate: null }, loss_cp: 0,
          similarity: "fixture", repertoire_line_count: 0, exact_transposition: false,
          example_line_id: null, example_line_name: null, preview_moves_uci: ["e2e4"],
          engine_version: "fixture", network_version: "fixture", depth: 14,
          report_id: "fixture", source_game_id: "fixture", source_ply: 0 }] } });
    } finally {
      activePreviews -= 1;
    }
  });
  let queueReads = 0;
  await page.route("**/api/queue/window?**", (route) => {
    queueReads += 1;
    return route.fulfill({ json: { local_date: "2026-09-18", count: 1, cards: [{
      id: "visual-card", queue_entry_id: 1, start_fen: startFen, moves: ["e2e4"],
      content_type: "opening", repertoire_name: "Spanish opening",
      repertoire_source: "PGN", trained_color: "white",
    }] } });
  });
  await page.route("**/api/cards/visual-card/review", (route) =>
    route.fulfill({ json: { persisted: true } }));

  try {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Correct" })).toBeVisible();
    await expect.poll(() => startedPreviews).toBeGreaterThanOrEqual(2);
    await page.waitForTimeout(300);
    expect(maximumActivePreviews).toBeLessThanOrEqual(2);
    expect(previewWorkClasses.every((workClass) => workClass === "background")).toBe(true);

    const refreshStartedAt = Date.now();
    await page.getByRole("button", { name: "Correct" }).click();
    await expect.poll(() => queueReads, { timeout: 2_000 }).toBeGreaterThanOrEqual(2);
    expect(Date.now() - refreshStartedAt).toBeLessThan(2_000);

    releasePreviews?.();
    await expect.poll(() => activePreviews).toBe(0);
    // Closed idle preparation deliberately selects only two entries per feed
    // refresh; foreground queue reads must not depend on draining all six.
    await page.waitForTimeout(3_500);
    expect(startedPreviews).toBe(2);
    await page.getByRole("button", { name: "Discoveries", exact: true }).click();
    await expect.poll(() => startedPreviews).toBeGreaterThan(2);
    expect(maximumActivePreviews).toBeLessThanOrEqual(2);
  } finally {
    releasePreviews?.();
  }
});
