import { test, expect, type Page } from "@playwright/test";
import { prepareVisualUI } from "./visual-fixtures";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const tacticFen = "q5nr/1ppknQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 w - - 1 18";

async function clickBoardSquare(page: Page, square: string): Promise<void> {
  const board = page.locator(".cg-wrap");
  await board.scrollIntoViewIfNeeded();
  const bounds = await board.boundingBox();
  if (!bounds) throw new Error("Training board is not visible");
  const black = (await page.locator(".board-frame").getAttribute("data-orientation")) === "black";
  const file = square.charCodeAt(0) - 97;
  const rank = Number(square[1]) - 1;
  await page.mouse.click(
    bounds.x + (((black ? 7 - file : file) + 0.5) * bounds.width) / 8,
    bounds.y + (((black ? rank : 7 - rank) + 0.5) * bounds.height) / 8,
  );
}

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

test("review saves stay quiet without moving the board or card", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: {
    local_date: "2026-09-18", count: 2, cards: [
      { id: "save-toast-first", queue_entry_id: 301, start_fen: startFen, moves: ["e2e4"], content_type: "opening", repertoire_name: "First card", repertoire_source: "PGN", attempt_state: "clean" },
      { id: "save-toast-second", queue_entry_id: 302, start_fen: startFen, moves: ["d2d4"], content_type: "opening", repertoire_name: "Second card", repertoire_source: "PGN", attempt_state: "clean" },
    ],
  } }));
  let releaseReview: (() => void) | undefined;
  await page.route("**/api/cards/save-toast-first/review", async route => {
    await new Promise<void>(resolve => { releaseReview = resolve; });
    await route.fulfill({ json: { persisted: true } });
  });
  await page.goto("/");
  const board = page.locator(".unified-board-shell-panel");
  const before = (await board.boundingBox())!;
  await page.getByRole("button", { name: "Correct" }).click();
  await expect.poll(() => Boolean(releaseReview)).toBe(true);
  await expect(page.locator(".notification-toast.notification-info, .notification-toast.notification-success")).toHaveCount(0);
  const during = (await board.boundingBox())!;
  expect(Math.abs(during.y - before.y)).toBeLessThanOrEqual(1);
  expect(Math.abs(during.x - before.x)).toBeLessThanOrEqual(1);
  releaseReview?.();
  await expect(page.getByText("Second card", { exact: true })).toBeVisible();
  await expect(page.locator(".notification-toast.notification-info, .notification-toast.notification-success")).toHaveCount(0);
  await page.getByRole("button", { name: "Notifications" }).click();
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page.locator(".notification-list")).toContainText("Result saved.");
});

test("completed tactic advances while an earlier review save is still pending", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    local_date: "2026-09-18", count: 3, cards: [
      { id: "first-overlap-browser", queue_entry_id: 201, start_fen: startFen,
        moves: ["e2e4"], content_type: "opening", repertoire_name: "First prep",
        repertoire_source: "PGN", attempt_state: "clean" },
      { id: "tactic-overlap-browser", queue_entry_id: 202, start_fen: tacticFen,
        moves: ["a2e6", "d7d8", "f7f8"], content_type: "tactic", repertoire_name: "Tactic",
        repertoire_source: "Lichess", attempt_state: "clean" },
      { id: "third-overlap-browser", queue_entry_id: 203, start_fen: startFen,
        moves: ["d2d4"], content_type: "opening", repertoire_name: "Third prep",
        repertoire_source: "PGN", attempt_state: "clean" },
    ],
  } }));
  let releaseFirstReview: (() => void) | undefined;
  const savedEntries: number[] = [];
  await page.route("**/api/cards/*/review", async (route) => {
    const requestBody = route.request().postDataJSON() as { queue_entry_id: number };
    savedEntries.push(requestBody.queue_entry_id);
    if (requestBody.queue_entry_id === 201)
      await new Promise<void>((resolve) => { releaseFirstReview = resolve; });
    await route.fulfill({ json: { persisted: true } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Correct" }).click();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", tacticFen);
  await clickBoardSquare(page, "a2");
  await clickBoardSquare(page, "e6");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /^q2k2nr\/.*3pB3.* w /);
  await clickBoardSquare(page, "f7");
  await clickBoardSquare(page, "f8");
  await expect(page.getByText("Third prep")).toBeVisible();
  expect(savedEntries).toEqual([201]);
  releaseFirstReview?.();
  await expect.poll(() => savedEntries).toEqual([201, 202]);
});

test("poisoned online A becomes inspectable while B and C save and conflict retry survives reload", async ({ page }) => {
  await page.addInitScript(() => {
    if (localStorage.getItem("recovery-fixture-seeded")) return;
    localStorage.setItem("recovery-fixture-seeded", "true");
    localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([
      { backendId: "poison-a", queueEntryId: 501, outcome: "correct", guided: false, attemptId: "original-a", completedAt: "2026-09-18T15:00:00Z", expectedRevision: 1 },
      { backendId: "valid-b", queueEntryId: 502, outcome: "correct", guided: false, attemptId: "original-b", completedAt: "2026-09-18T15:01:00Z" },
      { backendId: "valid-c", queueEntryId: 503, outcome: "correct", guided: false, attemptId: "original-c", completedAt: "2026-09-18T15:02:00Z" },
    ]));
  });
  const saved: string[] = [];
  const reconciliations: Array<{ key: string; body: unknown }> = [];
  let retrySucceeds = false;
  await prepareVisualUI(page);
  await page.route("**/api/cards/*/review", async route => {
    const body = route.request().postDataJSON();
    if (body.attempt_id === "original-a")
      return route.fulfill({ status: 409, json: { detail: "Historical failed save" } });
    saved.push(body.attempt_id);
    return route.fulfill({ json: { persisted: true } });
  });
  await page.route("**/api/cards/poison-a/review/reconcile", async route => {
    reconciliations.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
    return route.fulfill({ json: retrySucceeds ? { persisted: true } : { persisted: false,
      conflict: { code: "queue_attempt_unprovable", message: "The original queue attempt cannot be verified.", retryable: false } } });
  });
  await page.reload();
  await expect.poll(() => saved).toEqual(["original-b", "original-c"]);
  await expect(page.getByRole("button", { name: "Review conflicts (1)" })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Review conflicts (1)" }).click();
  const dialog = page.getByRole("dialog", { name: "Review conflicts" });
  await expect(dialog).toContainText("The original queue attempt cannot be verified.");
  const exported = JSON.parse(await dialog.getByRole("textbox", { name: "Conflict data" }).inputValue());
  expect(exported.onlineReviews[0]).toMatchObject({ attemptId: "original-a", completedAt: "2026-09-18T15:00:00Z", state: "conflicted" });
  retrySucceeds = true;
  await dialog.getByRole("button", { name: "Retry saved result" }).click();
  await expect(page.getByRole("button", { name: /Review conflicts/ })).toHaveCount(0);
  // Reload may resend an unacknowledged transport; it must retain its identity and result.
  expect([...new Set(reconciliations.map(item => item.key))]).toEqual(["review-reconcile:original-a:1", "review-reconcile:original-a:2"]);
  expect(reconciliations.filter(item => item.key === "review-reconcile:original-a:2")).toHaveLength(1);
  for (const reconciliation of reconciliations) expect(reconciliation.body).toEqual(reconciliations[0].body);
});

for (const format of ["numeric", "object"] as const) {
  test(`legacy ${format} guided marker blocks clean grading across reload until authoritative replay`, async ({ page }) => {
    await prepareVisualUI(page);
    await page.addInitScript(({ format }) => {
      if (localStorage.getItem("legacy-marker-fixture-seeded")) return;
      localStorage.setItem("legacy-marker-fixture-seeded", "true");
      localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify(format === "numeric"
        ? [801] : [{ queueEntryId: 801, operationId: "legacy-browser-guided-operation" }]));
    }, { format });
    let serverFailed = false;
    const markerKeys: string[] = [];
    let releaseMarkers!: () => void;
    const heldMarkerDelivery = new Promise<void>(resolve => { releaseMarkers = resolve; });
    const submittedReviews: unknown[] = [];
    await page.route("**/api/queue/window?**", route => route.fulfill({ json: {
      count: 1, local_date: "2026-09-18", cards: [{ id: "legacy-browser-card", revision: 1, queue_entry_id: 801,
        start_fen: startFen, moves: ["e2e4"], content_type: "opening", repertoire_name: "Legacy saved attempt",
        repertoire_source: "PGN", first_correct_at: "2026-09-17T12:00:00Z", attempt_failed: serverFailed }],
    } }));
    await page.route("**/api/queue/entries/801/fail", async route => {
      expect(route.request().postData()).toBeNull();
      markerKeys.push(route.request().headers()["idempotency-key"]);
      await heldMarkerDelivery;
      await route.fulfill({ json: { attempt_failed: true } });
    });
    await page.route("**/api/cards/*/review", async route => {
      submittedReviews.push(route.request().postDataJSON());
      await route.fulfill({ json: { persisted: true } });
    });
    try {
      await page.goto("/");
      await expect(page.getByRole("alert").filter({ hasText: "Guided attempt identity is pending" }).first()).toBeVisible();
      const normalizedMarker = await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-failures-v1")!));
      expect(normalizedMarker).toEqual([{ queueEntryId: 801, operationId: expect.any(String) }]);
      await page.reload();
      await expect(page.getByRole("alert").filter({ hasText: "Guided attempt identity is pending" }).first()).toBeVisible();
      await expect(page.getByRole("button", { name: "Correct", exact: true }).and(page.locator(":enabled"))).toHaveCount(0);
      await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "false");
      await page.keyboard.press("3");
      expect(submittedReviews).toEqual([]);
      expect(await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-failures-v1")!))).toEqual(normalizedMarker);
      expect(markerKeys.length).toBeGreaterThanOrEqual(2);
      expect(new Set(markerKeys)).toEqual(new Set([normalizedMarker[0].operationId]));
      serverFailed = true;
      releaseMarkers();
      await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-failures-v1")!))).toEqual([]);
      await page.reload();
      await expect(page.getByText("Legacy saved attempt", { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Finish on the board" })).toBeDisabled();
      await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
      expect(submittedReviews).toEqual([]);
    } finally { releaseMarkers(); }
  });
}
