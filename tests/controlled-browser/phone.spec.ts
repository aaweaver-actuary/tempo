import { test, expect, prepareControlledUI, openingCard } from "./fixtures";
import { noPageOverflow } from "../browser/ui-fixtures";
import { move } from "../browser/product-fixtures";
import { validateReview, receiptResponse } from "./contracts";

for (const viewport of [{ width: 390, height: 844 }]) {
  test(`Phone opening study shows repertoire identity above the board ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await prepareControlledUI(page, [openingCard("london", "London System", "d2d4", 1)]);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1, name: "Daily training", exact: true })).toHaveCount(1);
    const heading = page.locator(".shared-board-heading");
    await expect(heading.getByRole("heading", { name: "London System" })).toBeInViewport();
    await expect(heading.getByText("white to play", { exact: true })).toBeVisible();
    const board = page.locator(".persistent-board-shell .board-viewport");
    const [headingBounds, boardBounds] = await Promise.all([heading.boundingBox(), board.boundingBox()]);
    expect(headingBounds!.y + headingBounds!.height).toBeLessThanOrEqual(boardBounds!.y + 1);
    expect(boardBounds!.width).toBeGreaterThanOrEqual(Math.min(viewport.width - 24, 658) - 1);
    await expect(page.getByRole("heading", { name: "London System", exact: true })).toHaveCount(1);
    const toolbar = page.locator(".shared-board-toolbar");
    await expect(toolbar.getByRole("button", { name: "Keyboard shortcuts" })).toBeHidden();
    await expect(toolbar.getByRole("button", { name: "Flip board", exact: true })).toBeVisible();
    await expect(toolbar.getByRole("button", { name: /Show move/ })).toBeVisible();
    await expect(toolbar.locator("summary", { hasText: "More" })).toBeVisible();
    const details = page.locator(".study-details");
    await expect(details).not.toHaveAttribute("open", "");
    await expect(page.locator(".study-source")).toBeHidden();
    const boardFen = await page.locator(".board-frame").getAttribute("data-fen");
    await details.locator("summary").click();
    await expect(page.locator(".study-source")).toBeVisible();
    await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", boardFen!);
    await noPageOverflow(page);
  });
}

test("Phone pending review keeps one inline status until its original receipt confirms", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const api = await prepareControlledUI(page, [openingCard("pending-london", "London System", "d2d4", 41)], false);
  let complete = false;
  let submissions = 0;
  let originalOperation = "";
  for (const query of ([{ limit: "20" }, { limit: "20", include_opening_evidence: "true" }] as Record<string, string>[])) api.register({ method: "GET", path: "/api/queue/window", query }, route => {
    const cards = complete ? [openingCard("next-ruy", "Ruy Lopez", "e2e4", 42)]
      : [openingCard("pending-london", "London System", "d2d4", 41)];
    return route.fulfill({ json: { cards, count: cards.length } });
  });
  api.register({ method: "POST", path: "/api/cards/pending-london/review", query: {}, validate: request => validateReview(request, "pending-london", 41) }, async route => {
    submissions++;
    originalOperation = route.request().headers()["idempotency-key"];
    await route.fulfill({ status: 202, json: { operation_id: originalOperation, state: "queued" } });
  });
  api.register({ method: "GET", path: () => `/api/operations/${encodeURIComponent(originalOperation)}`, query: {} }, async route => {
    expect(decodeURIComponent(route.request().url().split("/api/operations/")[1])).toBe(originalOperation);
    await route.fulfill({ json: receiptResponse(complete ? "complete" : "queued", originalOperation) });
  });
  api.register({ method: "GET", path: "/api/cards/next-ruy/teaching", query: {} }, route => route.fulfill({ json: { states: [] } }));
  await page.goto("/");
  await move(page, "d2", "d4");
  await expect(page.getByRole("status").filter({ hasText: "Waiting for the computer to confirm this result." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Check save", exact: true })).toBeVisible();
  await expect(page.locator(".notification-toast")).toHaveCount(0);
  await expect(page.locator(".phone-study-heading h2")).toHaveText("London System");
  complete = true;
  // Automatic recovery checks the receipt without resubmitting the completed move.
  await expect(page.getByRole("button", { name: "Check save", exact: true })).toHaveCount(0, { timeout: 10000 });
  await expect(page.locator(".phone-study-heading h2")).toHaveText("Ruy Lopez");
  expect(submissions).toBe(1);
  await noPageOverflow(page);
});

test("Phone reload consumes a retained completed review receipt without another review POST", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const api = await prepareControlledUI(page, [openingCard("next-ruy", "Ruy Lopez", "e2e4", 812)], false);
  const traffic: string[] = [];
  api.register({ method: "POST", path: "/api/cards/reload-london/review", query: {}, validate: request => validateReview(request, "reload-london", 811) }, route => {
    traffic.push("POST");
    return route.fulfill({ status: 503, json: { detail: "A persisted review must not be replayed" } });
  });
  api.register({ method: "GET", path: "/api/operations/review-attempt%3Areload-original", query: {} }, route => {
    traffic.push("receipt");
    return route.fulfill({ json: receiptResponse("complete", "review-attempt:reload-original") });
  });
  await page.goto("/");
  await page.evaluate(() => localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([{
    backendId: "reload-london", queueEntryId: 811, attemptId: "reload-original", outcome: "correct", guided: false,
    completedAt: "2026-09-18T15:00:00Z",
  }])));
  await page.reload();
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]").length)).toBe(0);
  await expect(page.locator(".phone-study-heading h2")).toHaveText("Ruy Lopez");
  expect(traffic).toEqual(["receipt"]);
  await noPageOverflow(page);
});
