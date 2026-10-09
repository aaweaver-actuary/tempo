import { test, expect, navigate, noPageOverflow } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
import { move } from "./product-fixtures";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
function openingCard(id: string, title: string, firstMove: string, queueEntry: number) {
  return { id, queue_entry_id: queueEntry, start_fen: startFen, moves: [firstMove],
    content_type: "opening" as const, repertoire_name: title, repertoire_source: "White.pgn",
    first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" as const };
}

for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 767, height: 844 }]) {
  test(`Phone opening study shows repertoire identity above the board ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await prepareVisualUI(page, true, [openingCard("london", "London System", "d2d4", 1)]);
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

test("Phone study heading follows London-to-Ruy-Lopez card transitions", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page, true, [openingCard("london", "London System", "d2d4", 1), openingCard("ruy", "Ruy Lopez", "e2e4", 2)]);
  await expect(page.locator(".phone-study-heading h2")).toHaveText("London System");
  await move(page, "d2", "d4");
  // Completing the one-move line advances automatically after confirmed review.
  await expect(page.locator(".phone-study-heading h2")).toHaveText("Ruy Lopez");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", startFen);
  await move(page, "e2", "e4");
  await expect(page.locator(".feedback.complete")).toBeVisible();
});

test("Phone opening More supports keyboard help, restart, and edit without resetting on resize", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page, true, [openingCard("london", "London System", "d2d4", 1)]);
  const menu = page.locator(".phone-study-actions");
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: "Keyboard shortcuts" }).click();
  await expect(page.getByRole("dialog", { name: /Keyboard shortcuts/i })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu.locator("summary")).toBeFocused();
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: /Restart/ }).click();
  await expect(menu).not.toHaveAttribute("open", "");
  await expect(menu.locator("summary")).toBeFocused();
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: /Edit card/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  const boardInstance = page.locator(".cg-wrap");
  await boardInstance.evaluate(element => element.setAttribute("data-phone-instance", "original"));
  await page.setViewportSize({ width: 767, height: 844 });
  const expectTrainingHeadings = async () => {
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1, name: "Daily training", exact: true })).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 2, name: "London System", exact: true })).toHaveCount(1);
  };
  await expectTrainingHeadings();
  await page.setViewportSize({ width: 768, height: 1024 });
  await expect(page.locator(".phone-study-heading")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "London System", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Keyboard shortcuts" })).toBeVisible();
  await expect(boardInstance).toHaveAttribute("data-phone-instance", "original");
  await expectTrainingHeadings();
  await page.setViewportSize({ width: 767, height: 844 });
  await expectTrainingHeadings();
  await expect(page.locator(".phone-study-heading h2")).toHaveText("London System");
  await expect(boardInstance).toHaveAttribute("data-phone-instance", "original");
  await navigate(page, "Tactics");
  await expect(page.locator(".phone-study-heading")).toHaveCount(0);
  await expect(page.locator(".shared-board-toolbar").getByRole("button", { name: "Keyboard shortcuts" })).toBeVisible();
});

test("Long phone repertoire names wrap without hiding the board or overflowing", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await prepareVisualUI(page, true, [openingCard("long", "London System — tournament preparation with a very long repertoire name", "d2d4", 1)]);
  await expect(page.locator(".phone-study-heading h2")).toBeVisible();
  await noPageOverflow(page);
});

test("Phone pending review keeps one inline status until its original receipt confirms", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page, true, [openingCard("pending-london", "London System", "d2d4", 41)]);
  let complete = false;
  let submissions = 0;
  let originalOperation = "";
  await page.route("**/api/queue/window?**", route => {
    const cards = complete ? [openingCard("next-ruy", "Ruy Lopez", "e2e4", 42)]
      : [openingCard("pending-london", "London System", "d2d4", 41)];
    return route.fulfill({ json: { cards, count: cards.length } });
  });
  await page.route("**/api/cards/pending-london/review", async route => {
    submissions++;
    originalOperation = route.request().headers()["idempotency-key"];
    await route.fulfill({ status: 202, json: { operation_id: originalOperation, state: "queued" } });
  });
  await page.route("**/api/operations/**", async route => {
    expect(decodeURIComponent(route.request().url().split("/api/operations/")[1])).toBe(originalOperation);
    await route.fulfill({ json: complete ? { state: "complete", response: { persisted: true } } : { state: "queued" } });
  });
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

test("Phone actionable save warning keeps its severity and dismiss control readable", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await prepareVisualUI(page, true, [openingCard("blocked-london", "London System", "d2d4", 51)]);
  await page.route("**/api/cards/blocked-london/review", route => route.fulfill({ status: 202,
    json: { operation_id: "blocked-review", state: "queued" } }));
  await page.route("**/api/operations/blocked-review", route => route.fulfill({ json: {
    state: "blocked", last_error: { message: "Database access needs attention" } } }));
  await move(page, "d2", "d4");
  const toast = page.locator(".notification-toast");
  await expect(toast).toHaveCount(1);
  await expect(toast).toContainText("Saving is blocked.");
  await expect(toast.getByRole("button", { name: /Dismiss/ })).toBeInViewport();
  const severity = toast.locator(".notification-severity");
  expect(await severity.evaluate(element => element.clientHeight)).toBeLessThan(30);
  await noPageOverflow(page);
});


for (const reload of [false, true]) {
test(`Phone terminal review stays retained through idle recovery until explicit retry reload=${reload}`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page, true, [openingCard("terminal-london", "London System", "d2d4", 61)]);
  await page.clock.install();
  const submissions: { body: unknown; key: string }[] = [];
  let retryAllowed = false;
  await page.route("**/api/cards/terminal-london/review", route => {
    submissions.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
    return route.fulfill(retryAllowed ? { json: { persisted: true } }
      : { status: 422, json: { detail: "Invalid completed review", retryable: false } });
  });
  await page.route("**/api/operations/**", route => route.fulfill({ status: 404, json: {} }));
  await move(page, "d2", "d4");
  await page.clock.runFor(1500);
  await expect(page.getByRole("button", { name: "Retry save", exact: true })).toBeVisible();
  const readRetained = () => page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]"));
  await expect.poll(async () => (await readRetained())[0]?.automaticRecoverySuppressed).toBe("failed");
  const retained = await readRetained();
  await page.clock.fastForward(60000);
  expect(submissions).toHaveLength(1);
  expect(await readRetained()).toEqual(retained);
  if (reload) {
    await page.reload();
    await expect(page.getByRole("button", { name: "Check saved reviews", exact: true })).toBeVisible();
  }
  await page.clock.fastForward(60000);
  expect(submissions).toHaveLength(1);
  expect(await readRetained()).toEqual(retained);
  retryAllowed = true;
  await page.getByRole("button", { name: reload ? "Check saved reviews" : "Retry save", exact: true }).click();
  await expect.poll(readRetained).toEqual([]);
  expect(submissions).toHaveLength(2);
  expect(submissions[1]).toEqual(submissions[0]);
});
}

test("Phone idle review conflict releases Check save and permits the next real board move", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page, true, [openingCard("idle-conflict", "London System", "d2d4", 71)]);
  await page.clock.install();
  let conflictReady = false;
  const submissions: { body: unknown; key: string }[] = [];
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: { cards: conflictReady
    ? [openingCard("after-conflict", "Ruy Lopez", "e2e4", 72)]
    : [openingCard("idle-conflict", "London System", "d2d4", 71)], count: 1 } }));
  await page.route("**/api/cards/idle-conflict/review", route => {
    const key = route.request().headers()["idempotency-key"];
    submissions.push({ body: route.request().postDataJSON(), key });
    return route.fulfill({ status: 202, json: { operation_id: key, state: "queued" } });
  });
  await page.route("**/api/operations/**", route => {
    const operationId = decodeURIComponent(route.request().url().split("/api/operations/")[1]);
    return route.fulfill({ json: operationId.startsWith("review-reconcile:")
      ? { state: "complete", response: { persisted: false, conflict: {
        code: "queue_attempt_unprovable", message: "Original result needs review", retryable: false } } }
      : conflictReady ? { state: "failed", error: { status_code: 409, code: "queue_attempt_retired",
        retryable: false, detail: "Original entry retired" } } : { state: "queued" } });
  });
  await move(page, "d2", "d4");
  await page.clock.runFor(1500);
  await expect(page.getByRole("button", { name: "Check save", exact: true })).toBeVisible();
  const retained = await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]"));
  conflictReady = true;
  await page.clock.runFor(1500);
  await expect(page.getByRole("button", { name: "Check save", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Review conflicts/ })).toBeVisible();
  await expect(page.locator(".shared-board-heading")).toContainText("Ruy Lopez");
  expect(submissions).toHaveLength(1);
  const conflicted = await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]"));
  expect(conflicted[0]).toMatchObject({ ...retained[0], state: "conflicted" });
  let nextSubmissions = 0;
  await page.route("**/api/cards/after-conflict/review", route => {
    nextSubmissions++; return route.fulfill({ json: { persisted: true } });
  });
  await move(page, "e2", "e4");
  await page.clock.runFor(1000);
  await expect.poll(() => nextSubmissions).toBe(1);
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]").length)).toBe(1);
  await noPageOverflow(page);
});

test("Phone skipped same-card result stays paused with actionable earlier-result status", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page, true, [openingCard("skipped-card", "London System", "d2d4", 82),
    openingCard("unrelated-next", "Ruy Lopez", "e2e4", 83)]);
  const earlier = { backendId: "skipped-card", queueEntryId: 81, attemptId: "suppressed-phone-original",
    completedAt: "2026-10-08T12:00:00Z", outcome: "again", guided: false, automaticRecoverySuppressed: "failed" };
  await page.evaluate(review => {
    localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([review]));
    window.dispatchEvent(new Event("tempo:review-outbox"));
  }, earlier);
  let submissions = 0;
  await page.route("**/api/cards/skipped-card/review", route => { submissions++; return route.fulfill({ json: { persisted: true } }); });
  await move(page, "d2", "d4");
  const saveStatus = page.locator(".review-save-status");
  await expect(saveStatus).toBeVisible();
  await expect(saveStatus).toContainText("An earlier result for this card needs attention. Use Check saved reviews to resolve it first.");
  await expect(page.getByRole("button", { name: "Check saved reviews", exact: true })).toBeVisible();
  await expect(page.locator(".shared-board-heading")).toContainText("London System");
  await expect(page.getByText(/Result saved/)).toHaveCount(0);
  const retained = await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]"));
  expect(retained).toHaveLength(2);
  expect(retained[0]).toEqual(earlier);
  expect(retained[1]).toMatchObject({ backendId: "skipped-card", queueEntryId: 82, outcome: "correct" });
  expect(retained[1].attemptId).toBeTruthy();
  expect(submissions).toBe(0);
  await noPageOverflow(page);
});


test("Phone reload consumes a retained completed review receipt without another review POST", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page, true, [openingCard("next-ruy", "Ruy Lopez", "e2e4", 812)]);
  const traffic: string[] = [];
  await page.route("**/api/cards/reload-london/review", route => {
    traffic.push("POST");
    return route.fulfill({ status: 503, json: { detail: "A persisted review must not be replayed" } });
  });
  await page.route("**/api/operations/review-attempt%3Areload-original", route => {
    traffic.push("receipt");
    return route.fulfill({ json: { state: "complete", response: { persisted: true, review_id: 88 } } });
  });
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
