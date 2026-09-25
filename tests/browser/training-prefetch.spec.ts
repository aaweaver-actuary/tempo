import { test, expect } from "@playwright/test";
import { prepareVisualUI } from "./visual-fixtures";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

test("next training card paints before the previous review finishes saving", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    local_date: "2026-09-18", count: 2, cards: [
      { id: "first-prefetch", queue_entry_id: 101, start_fen: startFen,
        moves: ["e2e4"], content_type: "opening", repertoire_name: "First prep",
        repertoire_source: "PGN", attempt_state: "clean" },
      { id: "next-prefetch", queue_entry_id: 102, start_fen: startFen,
        moves: ["d2d4"], content_type: "opening", repertoire_name: "Next prep",
        repertoire_source: "PGN", attempt_state: "clean" },
    ],
  } }));
  let releaseReview: (() => void) | undefined;
  await page.route("**/api/cards/first-prefetch/review", async (route) => {
    await new Promise<void>((resolve) => { releaseReview = resolve; });
    await route.fulfill({ json: { persisted: true } });
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Correct" })).toBeVisible();
  const nextCardPaintMs = await page.evaluate(() => new Promise<number>((resolve) => {
    const started = performance.now();
    const observer = new MutationObserver(() => {
      if (document.body.textContent?.includes("Next prep")) {
        observer.disconnect();
        resolve(performance.now() - started);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    const correctButton = [...document.querySelectorAll("button")]
      .find((button) => button.textContent?.trim() === "Correct");
    correctButton?.click();
  }));
  expect(nextCardPaintMs).toBeLessThan(100);
  await expect(page.getByText("Next prep")).toBeVisible();
  expect(releaseReview).toBeDefined();
  releaseReview?.();
});
