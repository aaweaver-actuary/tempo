import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";

const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const prefixMoves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"];

function queuedPrefix(offerAvailable: boolean) {
  return {
    id: "browser-prefix", queue_entry_id: 1, start_fen: startingFen,
    moves: prefixMoves, content_type: "opening", kind: "prefix", revision: 3,
    repertoire_name: "Split test opening", repertoire_source: "PGN",
    trained_color: "white", recent_attempts_json: '["again","again","again"]',
    latest_failed_review_id: 7, prefix_split_offer_available: offerAvailable,
  };
}

const nextCard = {
  id: "browser-next", queue_entry_id: 2, start_fen: startingFen,
  moves: ["d2d4"], content_type: "opening", kind: "response", revision: 1,
  repertoire_name: "Next queued opening", repertoire_source: "PGN", trained_color: "white",
};

test("accepting a prefix split skips preview and advances to the next queued card", async ({ page }) => {
  await prepareVisualUI(page);
  let accepted = false;
  let previewRequests = 0;
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: {
    cards: accepted ? [nextCard] : [queuedPrefix(true), nextCard], count: accepted ? 1 : 2,
  } }));
  await page.route("**/api/cards/browser-prefix/prefix-split", route => {
    if (route.request().method() === "GET") previewRequests += 1;
    accepted = true;
    return route.fulfill({ json: {
      source_card_id: "browser-prefix", source_revision: 3,
      parent: { card_id: "browser-parent", starting_fen: startingFen, moves: prefixMoves.slice(0, 3), tested_player_moves: 2 },
      continuation: { card_id: "browser-child", starting_fen: startingFen, moves: prefixMoves.slice(3), tested_player_moves: 1 },
      applied: true, idempotent: false,
    } });
  });
  await page.reload();
  await expect(page.getByRole("button", { name: "Accept" })).toBeVisible();
  await page.getByRole("button", { name: "Accept" }).click();
  await expect(page.getByText("Next queued opening")).toBeVisible();
  expect(previewRequests).toBe(0);
});

test("rejecting a prefix split remains dismissed after queue reload", async ({ page }) => {
  await prepareVisualUI(page);
  let rejected = false;
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: {
    cards: [queuedPrefix(!rejected)], count: 1,
  } }));
  await page.route("**/api/cards/browser-prefix/prefix-split/reject", route => {
    rejected = true;
    return route.fulfill({ json: { rejected_after_review_id: 7 } });
  });
  await page.reload();
  await expect(page.getByRole("button", { name: "Reject" })).toBeVisible();
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByRole("button", { name: "Accept" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("Split test opening")).toBeVisible();
  await expect(page.getByRole("button", { name: "Accept" })).toHaveCount(0);
});
