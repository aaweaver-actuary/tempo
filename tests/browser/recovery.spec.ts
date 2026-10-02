import { test, expect, navigate, prepareUI } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
import { prepareRepairUI, repairStartFen } from "./repair-fixtures";
import { expectedPieces, renderedPieces, playMove, squareCenter } from "./keyboard-fixtures";
import { Chess } from "chess.js";

test("repair retries survive delayed operation and task transitions after reload without another source edit", async ({ page }) => {
  const repair = await prepareRepairUI(page);
  const reviewedCards: string[] = [];
  page.on("request", request => {
    const match = new URL(request.url()).pathname.match(/^\/api\/cards\/([^/]+)\/review$/);
    if (request.method() === "POST" && match) reviewedCards.push(match[1]);
  });
  let operationAdvanced = false, operationRetryPosts = 0, oldReceiptPolls = 0;
  let taskRetryId = "", taskRetryPosts = 0, pendingTaskPolls = 0, taskRetryApplied = false;
  let validation: "waiting" | "failed" | "complete" = "waiting";
  await page.route("**/api/operations/*", route => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1)!;
    if (id === taskRetryId) {
      pendingTaskPolls++;
      return route.fulfill({ json: taskRetryApplied ? { state: "complete",
        response: { id: "repair-graph", generation: 2, state: "queued" } } : { state: "queued" } });
    }
    if (id !== repair.operationIds[0]) return route.fulfill({ json: { state: "unknown" } });
    if (!repair.accepted) return route.fulfill({ json: { state: "unknown" } });
    if (!operationAdvanced) {
      if (operationRetryPosts) oldReceiptPolls++;
      return route.fulfill({ json: { state: "blocked", retry_cycle: 1, attempt_count: 5, cycle_attempt_count: 5,
        last_error: { message: "Retryable save failure" } } });
    }
    return route.fulfill({ json: { state: "complete", retry_cycle: 2, attempt_count: 6,
      response: { task_id: "repair-graph", task_generation: 2, repertoire_id: "repair-repertoire",
        issue_id: "repair-issue", state: "queued" } } });
  });
  await page.route("**/api/operations/*/retry", route => {
    operationRetryPosts++;
    return route.fulfill({ status: 202, json: { state: "blocked" } });
  });
  await page.route("**/api/system/tasks/repair-graph/retry", route => {
    taskRetryPosts++; taskRetryId = route.request().headers()["idempotency-key"];
    return route.fulfill({ status: 202, json: { operation_id: taskRetryId, state: "queued" } });
  });
  await page.route("**/api/repertoires/repair-repertoire/integrity/repairs/repair-graph?**", route =>
    route.fulfill({ json: { task_id: "repair-graph", task_generation: taskRetryApplied ? 3 : 2, state: validation,
      issue_count: validation === "complete" ? 0 : 1, reason: validation === "failed" ? "Retryable graph failure" : null,
      retry_task_id: validation === "failed" ? "repair-graph" : null } }));
  await page.getByRole("button", { name: "Resume repair" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "e4", exact: true }).click();
  await page.getByRole("button", { name: "Keep this response" }).click();
  await expect.poll(() => repair.operationIds.length).toBe(1);
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-integrity-repairs-v2")!)[0]?.attempts ?? 0)).toBeGreaterThan(0);
  await page.clock.setFixedTime(new Date("2026-09-18T16:00:10Z"));
  await page.reload();
  const retryButton = page.getByRole("button", { name: "Retry repair", exact: true });
  await expect(retryButton).toBeVisible();
  await retryButton.click();
  await expect(page.getByText("Repair saving", { exact: true })).toBeVisible();
  await page.reload();
  await expect.poll(() => oldReceiptPolls).toBeGreaterThanOrEqual(2);
  await expect(page.getByText("Repair saving", { exact: true })).toBeVisible();
  operationAdvanced = true;
  await expect(page.getByText("Repair validating", { exact: true })).toBeVisible();
  validation = "failed";
  await expect(retryButton).toBeVisible(); await retryButton.click();
  await expect.poll(() => taskRetryPosts).toBe(1);
  await page.reload();
  await expect.poll(() => pendingTaskPolls).toBeGreaterThanOrEqual(2);
  await expect(page.getByText("Repair validating", { exact: true })).toBeVisible();
  taskRetryApplied = true; validation = "waiting";
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-integrity-repairs-v2")!)[0]?.taskGeneration)).toBe(3);
  const studyBoard = page.locator(".persistent-board-shell .board-frame");
  await playMove(page, studyBoard, "e2", "e4");
  // Completing this one-move card grades it automatically. An additional click
  // can arrive after that advancement and incorrectly grade the next card.
  await expect(page.getByRole("heading", { name: "Second study card" })).toBeVisible();
  const positionBeforeConfirmation = await studyBoard.getAttribute("data-fen");
  validation = "complete"; repair.confirmed = true;
  await expect(page.getByText("Repair validating", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(studyBoard).toHaveAttribute("data-fen", positionBeforeConfirmation!);
  await expect(page.getByRole("heading", { name: "Second study card" })).toBeVisible();
  expect(operationRetryPosts).toBe(1); expect(taskRetryPosts).toBe(1);
  expect(repair.operationIds).toHaveLength(1);
  expect(reviewedCards).toEqual(["study-one"]);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-integrity-repairs-v2")!))).toEqual([]);
});

test("guided repair previews real arrows and pieces, saves durably, and preserves study through reload and confirmation", async ({ page }) => {
  const repair = await prepareRepairUI(page);
  await page.getByRole("button", { name: "Resume repair" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Suggested response: e4")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Keep this response" })).toBeDisabled();
  const response = dialog.getByRole("button", { name: "e4", exact: true });
  await response.hover();
  const previewBoard = dialog.locator(".board-frame");
  await expect(previewBoard.locator("svg.cg-shapes > g > g[cgHash]")).toHaveCount(1);
  await response.click();
  await expect(response.locator("xpath=ancestor::tr")).toHaveAttribute("aria-selected", "true");
  expect(await response.locator("xpath=ancestor::tr").evaluate(element => getComputedStyle(element).backgroundColor))
    .not.toBe(await dialog.getByRole("button", { name: "d4", exact: true }).locator("xpath=ancestor::tr").evaluate(element => getComputedStyle(element).backgroundColor));
  await dialog.getByRole("button", { name: "Next move" }).click();
  const previewPosition = new Chess(repairStartFen); previewPosition.move("e2e4");
  await expect.poll(() => renderedPieces(previewBoard)).toEqual(expectedPieces(previewPosition.fen()));
  await dialog.getByRole("button", { name: "Decision position" }).click();
  await expect.poll(() => renderedPieces(previewBoard)).toEqual(expectedPieces(repairStartFen));
  await dialog.getByRole("button", { name: "Keep this response" }).click();
  await expect(dialog).toHaveCount(0);
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-integrity-repairs-v2")!)[0]);
  expect(saved.selectedMoveUci).toBe("e2e4");
  await expect.poll(() => repair.operationIds.length).toBe(1);
  expect(repair.operationIds[0]).toBe(saved.operationId);
  const studyBoard = page.locator(".persistent-board-shell .board-frame");
  await playMove(page, studyBoard, "e2", "e4");
  await page.getByRole("button", { name: "Correct", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Second study card" })).toBeVisible();
  // Advance the fixture's fixed wall clock beyond the durable retry deadline.
  await page.clock.setFixedTime(new Date("2026-09-18T16:00:10Z"));
  await page.reload();
  await expect(page.getByText("Repair validating", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(repair.operationIds).toEqual([saved.operationId]);
  expect(repair.receiptIds).toContain(saved.operationId);
  const from = await squareCenter(studyBoard, "d2"), to = await squareCenter(studyBoard, "d4");
  const studyFocus = page.locator(".persistent-board-shell .board-viewport");
  await studyFocus.focus();
  await page.mouse.move(from.x, from.y); await page.mouse.down(); await page.mouse.move(to.x, to.y);
  await expect(studyBoard.locator("piece.dragging")).toHaveCount(1);
  await studyBoard.evaluate(element => element.setAttribute("data-preserved-board", "true"));
  const beforeFen = await studyBoard.getAttribute("data-fen");
  repair.confirmed = true;
  await expect(page.getByText("Repair validating", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Resume repair" })).toHaveCount(0);
  await expect(studyBoard).toHaveAttribute("data-preserved-board", "true");
  await expect(studyBoard).toHaveAttribute("data-fen", beforeFen!);
  await expect(studyBoard.locator("piece.dragging")).toHaveCount(1);
  await expect(studyFocus).toBeFocused();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.mouse.up();
  await expect.poll(() => studyBoard.getAttribute("data-fen")).not.toBe(beforeFen);
});
import type { Page } from "@playwright/test";

const startFen =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

test("training startup does not preload unrelated workspace requests", async ({ page }) => {
  const requestedPaths: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/")) requestedPaths.push(path);
  });
  await prepareVisualUI(page);
  await expect(page.getByText("Spanish opening", { exact: true }).first()).toBeVisible();
  await page.waitForTimeout(400);
  expect(requestedPaths).toContain("/api/repertoire/lines");
  for (const unrelatedPath of ["/api/games/summary", "/api/progress", "/api/endgames/templates"])
    expect(requestedPaths).not.toContain(unrelatedPath);
});

test("reopening a saved guided card without input does not record a new failure or show a red X", async ({ page }) => {
  await prepareVisualUI(page);
  let failureRequests = 0;
  await page.route("**/api/queue/entries/*/fail", async (route) => {
    failureRequests += 1;
    await route.fulfill({ json: { attempt_failed: true } });
  });
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    count: 1,
    cards: [{
      id: "resumed-card", queue_entry_id: 1974, start_fen: startFen,
      moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
      repertoire_name: "Resumed", repertoire_source: "PGN", trained_color: "white",
      attempt_failed: true,
    }],
  } }));
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.getByRole("heading", { name: "Resumed" })).toBeVisible();
  await expect(page.getByText("Guided attempt resumed")).toBeVisible();
  await expect(page.locator(".outcome-flash.wrong")).toHaveCount(0);
  expect(failureRequests).toBe(0);
});

test("intentional training failure is saved once and reload resumes it without another failure", async ({ page }) => {
  await prepareVisualUI(page);
  let failureRequests = 0;
  let savedFailure = false;
  await page.route("**/api/queue/entries/*/fail", async (route) => {
    failureRequests += 1;
    savedFailure = true;
    await route.fulfill({ json: { attempt_failed: true } });
  });
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    count: 1,
    cards: [{
      id: "intentional-card", queue_entry_id: 1975, start_fen: startFen,
      moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
      repertoire_name: "Intentional", repertoire_source: "PGN", trained_color: "white",
      attempt_failed: savedFailure,
    }],
  } }));
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.getByRole("heading", { name: "Intentional" })).toBeVisible();
  await page.getByRole("button", { name: /Show move/ }).click();
  await expect.poll(() => failureRequests).toBe(1);
  await expect.poll(() => page.evaluate(() =>
    localStorage.getItem("tempo-pending-training-failures-v1"))).toBe("[]");
  await page.reload();
  await expect(page.getByText("Guided attempt resumed")).toBeVisible();
  await expect(page.locator(".outcome-flash.wrong")).toHaveCount(0);
  expect(failureRequests).toBe(1);
});

test("unresolved guided review keeps its card paused while other training opens", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/queue/entries/1974/fail", (route) => route.fulfill({
    status: 409, json: { detail: "This queue attempt is no longer active" },
  }));
  await page.route("**/api/cards/pending-card/review", (route) => route.fulfill({
    status: 409, json: { detail: "This queue attempt is no longer available" },
  }));
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    count: 2, cards: [
      { id: "pending-card", queue_entry_id: 1974, start_fen: startFen,
        moves: ["e2e4"], content_type: "opening", repertoire_name: "Pending review",
        repertoire_source: "PGN", trained_color: "white" },
      { id: "available-card", queue_entry_id: 1975, start_fen: startFen,
        moves: ["d2d4"], content_type: "opening", repertoire_name: "Available review",
        repertoire_source: "PGN", trained_color: "white" },
    ],
  } }));
  await page.evaluate(() => {
    localStorage.clear();
    localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([
      { backendId: "pending-card", queueEntryId: 1974, outcome: "correct", guided: true },
    ]));
  });
  await page.reload();

  await expect(page.getByRole("heading", { name: "Available review" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Pending review" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry saving review" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Correct" })).toBeEnabled();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]"))).toHaveLength(1);
});

test("legacy discovery timeout reopens as an unconfirmed save and retries the same choice", async ({ page }) => {
  await prepareVisualUI(page);
  let finishAccept: (() => void) | undefined;
  let acceptedMove = "";
  await page.route("**/api/discoveries/legacy-timeout/accept", async (route) => {
    acceptedMove = (route.request().postDataJSON() as { selected_move_uci: string }).selected_move_uci;
    await new Promise<void>((resolve) => { finishAccept = resolve; });
    await route.fulfill({ status: 202, json: { status: "preparing", intent_id: "intent" } });
  });
  await page.route("**/api/discovery-admissions/intent", (route) =>
    route.fulfill({ json: { state: "queued", error: null } }));
  await page.evaluate(() => localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{
    opportunityId: "legacy-timeout", selectedMoveUci: "g1f3",
    evidenceFingerprint: "revision", state: "failed",
    error: "Discovery save timed out after 15 seconds. Retry save.",
  }])));
  await page.reload();
  await expect(page.getByText(/Discovery save unconfirmed; Tempo will retry/)).toBeVisible();
  await expect(page.getByText(/Discovery save failed:/)).toHaveCount(0);
  await expect.poll(() => acceptedMove).toBe("g1f3");
  finishAccept?.();
  await expect(page.getByText(/Discovery save unconfirmed; Tempo will retry/)).toHaveCount(0);
});

test("legacy long discovery save key recovers and queues the saved choice", async ({ page }) => {
  await prepareVisualUI(page);
  const opportunityId = "a".repeat(64);
  const evidenceFingerprint = "b".repeat(64);
  const requestKeys: string[] = [];
  await page.route(`**/api/discoveries/${opportunityId}/accept`, (route) => {
    const key = route.request().headers()["idempotency-key"] ?? "";
    requestKeys.push(key);
    if (key.length > 128) return route.fulfill({ status: 422,
      json: { detail: "Idempotency-Key must be at most 128 characters" } });
    return route.fulfill({ status: 202, json: { status: "preparing", intent_id: "recovered-intent" } });
  });
  await page.route("**/api/discovery-admissions/recovered-intent", (route) =>
    route.fulfill({ json: { state: "queued", error: null } }));
  await page.evaluate(({ opportunityId: id, evidenceFingerprint: fingerprint }) =>
    localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{
      opportunityId: id, selectedMoveUci: "g1f3", evidenceFingerprint: fingerprint,
      state: "failed", error: "Idempotency-Key must be at most 128 characters",
    }])), { opportunityId, evidenceFingerprint });
  await page.reload();
  await expect.poll(() => requestKeys.length).toBe(1);
  expect(requestKeys[0].length).toBeLessThanOrEqual(128);
  await expect.poll(() => page.evaluate(() =>
    JSON.parse(localStorage.getItem("tempo-pending-discovery-admissions-v1") ?? "[]").length)).toBe(0);
});

test("reloaded prefetched guided card waits for the earlier review before marking failure", async ({ page }) => {
  await prepareVisualUI(page);
  let finishEarlierReview: (() => void) | undefined;
  let earlierReviewRequests = 0;
  let guidedFailureRequests = 0;
  let guidedFailureSaved = false;
  await page.route("**/api/cards/earlier-card/review", async (route) => {
    earlierReviewRequests += 1;
    await new Promise<void>((resolve) => { finishEarlierReview = resolve; });
    await route.fulfill({ json: { persisted: true } });
  });
  await page.route("**/api/queue/entries/43/fail", async (route) => {
    guidedFailureRequests += 1;
    guidedFailureSaved = true;
    await route.fulfill({ json: { attempt_failed: true } });
  });
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    count: 1, cards: [{ id: "next-card", queue_entry_id: 43, start_fen: startFen,
      moves: ["e2e4"], content_type: "opening", repertoire_name: "Next guided card",
      repertoire_source: "PGN", trained_color: "white", attempt_failed: guidedFailureSaved }],
  } }));
  await page.evaluate(() => {
    localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([{
      backendId: "earlier-card", queueEntryId: 42, outcome: "correct", guided: false,
    }]));
    localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify([43]));
  });
  await page.reload();
  await expect.poll(() => earlierReviewRequests).toBe(1);
  expect(guidedFailureRequests).toBe(0);
  finishEarlierReview?.();
  await expect.poll(() => guidedFailureRequests).toBe(1);
  await expect(page.getByText("Guided attempt resumed")).toBeVisible();
  await page.reload();
  await expect(page.getByText("Guided attempt resumed")).toBeVisible();
  expect(earlierReviewRequests).toBe(1);
  expect(guidedFailureRequests).toBe(1);
});

test("unavailable repertoire lines do not falsely grade another legal move", async ({ page }) => {
  await prepareVisualUI(page);
  // Initial service worker activation can reload the page; finish it before injecting the failure.
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  await page.route("**/api/repertoire/lines", (route) => route.fulfill({ status: 503, json: { detail: "Temporarily unavailable" } }));
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.getByRole("button", { name: "Retry loading lines" })).toBeVisible();
  await expect(page.getByText("Spanish opening", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Bury", exact: true })).toBeVisible();
  const board = (await page.locator(".cg-wrap").boundingBox())!;
  for (const rank of [6, 4])
    await page.mouse.click(board.x + (3.5 * board.width) / 8, board.y + ((rank + 0.5) * board.height) / 8);
  await expect(page.getByText(/Cannot verify another repertoire move until lines load/)).toBeVisible();
  await expect(page.getByText(/Again recorded/)).toHaveCount(0);
  await page.unroute("**/api/repertoire/lines");
  await page.getByRole("button", { name: "Retry loading lines" }).click();
  await expect(page.getByRole("button", { name: "Retry loading lines" })).toHaveCount(0);
});

test("check coverage loads adaptive settings without a validation alert", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/repertoires/visual-repertoire/coverage", (route) =>
    route.fulfill({ json: {
      run_id: "80bffd5b-ff52-469f-8096-36cc8518b07b",
      status: "complete",
      required_branches: 2,
      covered_branches: 1,
      probability_coverage: 0.9,
      is_complete: false,
      unknown_nodes: 0,
      last_error: null,
      settings: {
        automatic_priority: true,
        reply_denominator: 100,
        cumulative_target: 0.95,
        horizon_fullmoves: 15,
        path_floor: 0.0005,
        maia_elo: 1500,
        explorer_rating: 1400,
        recent_median_rating: 1500,
        speed_weights: { rapid: 1 },
        cohort_games: 12,
      },
    } }),
  );
  await page.route("**/api/repertoires/visual-repertoire/coverage/gaps", (route) =>
    route.fulfill({ json: { gaps: [] } }),
  );
  await navigate(page, "Repertoire");
  await page.getByRole("button", { name: "Check coverage" }).click();
  await expect(page.getByText("1 / 2")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("fractional priority evidence loads both repertoires without a diagnostic", async ({
  page,
}) => {
  await prepareVisualUI(page);
  await page.route("**/api/repertoires", (route) =>
    route.fulfill({
      json: {
        repertoires: [
          {
            id: "black",
            name: "Keep It Simple for Black",
            source_name: "black.pgn",
            line_count: 537,
            card_count: 399,
            due_count: 22,
            introduction_priority: {
              state: "partial",
              personal_games: 637.74,
              explorer: "unknown",
              maia: "unknown",
              updated_at: "2026-09-21T00:56:50.367084+00:00",
              error: null,
            },
          },
          {
            id: "white",
            name: "London System",
            source_name: "white.pgn",
            line_count: 1569,
            card_count: 813,
            due_count: 55,
            introduction_priority: {
              state: "partial",
              personal_games: 299.74,
              explorer: "unknown",
              maia: "unknown",
              updated_at: "2026-09-21T00:55:11.336135+00:00",
              error: null,
            },
          },
        ],
      },
    }),
  );
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await navigate(page, "Repertoire");
  await expect(page.getByText("Keep It Simple for Black")).toBeVisible();
  await expect(page.getByText("London System")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("unaffected opening reviews remain mixed with tactics during repertoire repair", async ({
  page,
}) => {
  await prepareVisualUI(page);
  await page.route("**/api/repertoires", (route) =>
    route.fulfill({
      json: {
        repertoires: [
          {
            id: "repair-rep",
            name: "Repair repertoire",
            source_name: "repair.pgn",
            line_count: 3,
            card_count: 3,
            due_count: 1,
            blocked_due_count: 1,
            blocked_card_count: 1,
            integrity_status: "needs_repair",
            integrity_issue_count: 1,
          },
        ],
      },
    }),
  );
  await page.route("**/api/queue/window?**", (route) =>
    route.fulfill({
      json: {
        count: 2,
        cards: [
          {
            id: "opening-review",
            queue_entry_id: 10,
            start_fen: startFen,
            moves: ["e2e4"],
            content_type: "opening",
            repertoire_name: "Repair repertoire",
            repertoire_source: "repair.pgn",
            trained_color: "white",
          },
          {
            id: "tactic-review",
            queue_entry_id: 11,
            start_fen: startFen,
            moves: ["d2d4"],
            content_type: "tactic",
            repertoire_name: "Tactics",
            repertoire_source: "Puzzle",
            trained_color: "white",
          },
        ],
      },
    }),
  );
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.locator(".session-count strong")).toHaveText("2");
  await expect(page.getByText("cards left", { exact: true })).toBeVisible();
  await expect(
    page.getByText("1 opening card paused by repertoire repair."),
  ).toBeVisible();
  await expect(
    page.getByText(/Unaffected openings and tactics remain available/),
  ).toBeVisible();
});
test("failed initial loads never display empty records or zero statistics", async ({
  page,
}) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Local database unavailable. Check the data mount." },
    }),
  );
  await prepareUI(page);
  for (const workspace of ["Games", "Progress"]) {
    await navigate(page, workspace);
    await expect(page.getByRole("alert").first()).toBeVisible();
    await expect(page.locator(".games-metrics,.metric-grid")).toHaveCount(0);
    await expect(
      page.getByText("No games imported", { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Retry", exact: true }),
    ).toBeVisible();
  }
});
test("failed settings reads cannot overwrite authoritative settings with defaults", async ({
  page,
}) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 503, json: { detail: "Database unavailable" } }),
  );
  await prepareUI(page);
  await navigate(page, "Settings");
  await expect(page.getByRole("alert").first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Save settings" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Retry", exact: true }),
  ).toBeVisible();
});

test("malformed progress is unavailable and retry recovers real measurements", async ({
  page,
}) => {
  let broken = true;
  await page.route("**/api/progress", (route) =>
    route.fulfill({
      json: broken
        ? { unexpected: true }
        : {
            states: { new: 2 },
            activity: [],
            reviewedToday: 3,
            cleanCards: 2,
            dueToday: 1,
            totalCards: 9,
          },
    }),
  );
  await prepareUI(page);
  await navigate(page, "Progress");
  await expect(page.getByRole("alert").first()).toBeVisible();
  await expect(page.locator(".metric-grid")).toHaveCount(0);
  broken = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.locator(".metric-grid")).toContainText("9");
});

test("frontend errors show a redacted copyable debug bundle", async ({ page }) => {
  await page.route("**/api/progress", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Database unavailable" },
    }),
  );
  await prepareUI(page);
  await page.evaluate(() => {
    Object.defineProperty(window, "__tempoCopiedDebug", {
      configurable: true,
      value: "",
      writable: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value: string) => {
          (window as unknown as { __tempoCopiedDebug: string }).__tempoCopiedDebug = value;
        },
      },
    });
  });
  await navigate(page, "Progress");
  await expect(page.getByRole("alert").first()).toBeVisible();
  await page.getByRole("button", { name: "Notifications" }).click();
  await expect(page.getByRole("button", { name: "Copy debug info" })).toBeVisible();
  await page.getByRole("button", { name: "Copy debug info" }).click();
  await expect(page.getByRole("button", { name: "Copied debug info" })).toBeVisible();
  const copied = await page.evaluate(
    () => JSON.parse((window as unknown as { __tempoCopiedDebug: string }).__tempoCopiedDebug),
  );
  expect(copied.schemaVersion).toBe(1);
  expect(copied.error.message).toContain("/api/progress");
  expect(copied.workspace.activeView).toBe("insights");
  expect(copied.omitted).toContain("PGN and repertoire lines");
});

test('import waits for saved settings before writing a repertoire', async ({page}) => {
  let releaseSettings!: () => void;
  const pendingSettings = new Promise<void>(resolve => { releaseSettings = resolve; });
  await page.route('**/api/settings', async route => {
    await pendingSettings;
    await route.fulfill({json:{initial_depth:2,timezone:'local',new_cards_per_day:2,lichess_username:'',chesscom_username:'',auto_sync_minutes:3,engine_line_window_cp:30,major_mistake_cp:100,light_first_interval_days:7,draw_hold_user_moves:20}});
  });
  await prepareUI(page);
  await navigate(page,'Repertoire'); await page.getByRole('button',{name:/Import PGN/}).click();
  await page.locator('input[type=file]').setInputFiles({name:'short.pgn',mimeType:'application/x-chess-pgn',buffer:Buffer.from('1. d4 d5 2. c4 e6 *')});
  await expect(page.getByRole('button',{name:'Import repertoire',exact:true})).toBeDisabled();
  releaseSettings();
  await expect(page.getByRole('button',{name:'Import repertoire',exact:true})).toBeEnabled();
  await expect(page.getByText('2 user moves',{exact:true})).toBeVisible();
});

async function preparePgnReceiptCompletion(page: Page) {
  await page.route("**/api/repertoires", route => route.fulfill({ json: { repertoires: [{
    id: "visual-repertoire", name: "Spanish opening", source_name: "Spanish.pgn", line_count: 1,
    card_count: 1, due_count: 1, trained_color: "white", integrity_status: "clean",
    graph_state: "ready", graph_updated_at: "2026-09-18T12:00:00Z",
  }] } }));
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: { cards: [], projection: {
    state: "ready", generation: 1, updated_at: "2026-09-18T12:00:01Z", refresh_pending: false, last_error: null,
  } } }));
}

test("unknown PGN receipt recovers the same operation after reload and matching file reselection", async ({ page }) => {
  await prepareVisualUI(page);
  await preparePgnReceiptCompletion(page);
  const postedKeys: string[] = [];
  const postedBodies: string[] = [];
  const completedImport = { repertoire_id: "visual-repertoire", source_name: "recovery.pgn", games_found: 1, unique_lines: 1, cards_created: 1, duplicates_merged: 0, cards_admitted_today: 0 };
  await page.route("**/api/imports/pgn", async route => {
    postedKeys.push(route.request().headers()["idempotency-key"]);
    postedBodies.push(route.request().postData() ?? "");
    await route.fulfill(postedKeys.length === 1
      ? { status: 202, json: { operation_id: postedKeys[0], state: "unknown" } }
      : { json: completedImport });
  });
  await page.route("**/api/operations/*", route => route.fulfill({ json: { operation_id: postedKeys[0], state: "unknown", message: "No durable receipt exists yet" } }));
  await navigate(page, "Repertoire");
  await page.getByRole("button", { name: /Import PGN/ }).click();
  await page.locator('input[type="file"]').setInputFiles({ name: "recovery.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from("1. e4 e5 2. Nf3 *") });
  await expect(page.getByRole("button", { name: "Import repertoire", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Import repertoire", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText("confirmation is unavailable");
  await expect(page.getByRole("dialog").getByRole("alert")).toHaveCount(0);
  const remembered = await page.evaluate(() => localStorage.getItem("tempo-pending-pgn-import-v1"));
  expect(JSON.parse(remembered!).operationId).toBe(postedKeys[0]);
  await page.getByRole("button", { name: "Close import dialog", exact: true }).click();
  await page.reload();
  await navigate(page, "Repertoire");
  await page.getByRole("button", { name: /Import PGN/ }).click();
  await page.locator('input[type="file"]').setInputFiles({ name: "recovery.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from("1. e4 e5 2. Nf3 *") });
  await expect(page.getByRole("button", { name: "Import repertoire", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Import repertoire", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Imported", exact: true })).toBeVisible();
  expect(postedKeys).toHaveLength(2);
  expect(postedKeys[1]).toBe(postedKeys[0]);
  for (const body of postedBodies) {
    expect(body).toContain('filename="recovery.pgn"');
    expect(body).toContain("1. e4 e5 2. Nf3 *");
    expect(body).toContain('name="trained_color"\r\n\r\nwhite');
    expect(body).toContain('name="initial_depth"\r\n\r\n6');
  }
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-pgn-import-v1"))).toBeNull();
});

test("legacy pending PGN import promptly shows its diagnostic and Check again only inspects the original operation", async ({ page }) => {
  await page.clock.install();
  await prepareVisualUI(page, false);
  const diagnostic = "Legacy receipt has no saved payload. Recover only from matching journal or outbox evidence; automatic replay is unavailable.";
  const storedIdentity = await page.evaluate(async () => {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("1. e4 e5 2. Nf3 *"));
    const fingerprint = ["legacy.pgn", "white", 6,
      Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("")].join(":");
    const stored = JSON.stringify({ operationId: "original-import", fingerprint });
    localStorage.setItem("tempo-pending-pgn-import-v1", stored);
    return stored;
  });
  const inspectedOperations: string[] = [];
  let posts = 0;
  await page.route("**/api/imports/pgn", route => {
    posts += 1;
    return route.abort();
  });
  await page.route("**/api/operations/**", route => {
    expect(route.request().method()).toBe("GET");
    inspectedOperations.push(new URL(route.request().url()).pathname);
    return route.fulfill({ json: { operation_id: "original-import", state: "pending", message: diagnostic } });
  });
  await navigate(page, "Repertoire");
  await page.getByRole("button", { name: /Import PGN/ }).click();
  await page.locator('input[type="file"]').setInputFiles({ name: "legacy.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from("1. e4 e5 2. Nf3 *") });
  await expect(page.getByRole("button", { name: "Import repertoire", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Import repertoire", exact: true }).click();
  const dialog = page.getByRole("dialog");
  // No deadline advancement: the backend diagnostic must finish checking promptly.
  await expect(dialog.getByRole("status")).toHaveText(`Import confirmation is unavailable. ${diagnostic}`);
  await expect(page.getByRole("button", { name: "Check again", exact: true })).toBeEnabled();
  expect(inspectedOperations).toEqual(["/api/operations/original-import"]);
  await page.getByRole("button", { name: "Check again", exact: true }).click();
  await expect(page.getByRole("button", { name: "Check again", exact: true })).toBeEnabled();
  await expect(dialog.getByRole("status")).toHaveText(`Import confirmation is unavailable. ${diagnostic}`);
  expect(inspectedOperations).toEqual(["/api/operations/original-import", "/api/operations/original-import"]);
  expect(posts).toBe(0);
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Imported", exact: true })).toHaveCount(0);
  await expect(page.locator('input[type="file"]')).toHaveJSProperty("value", "C:\\fakepath\\legacy.pgn");
  await expect(dialog.getByText("6 user moves", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-pgn-import-v1"))).toBe(storedIdentity);
});

for (const state of ["executing", "retrying"]) {
  test(`durably ${state} PGN import waits informationally and Check again never resends`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 844 });
    await page.clock.install();
    await prepareVisualUI(page, false);
    await preparePgnReceiptCompletion(page);
    let operationId = "";
    let complete = false;
    let posts = 0;
    let receiptReads = 0;
    await page.route("**/api/imports/pgn", route => {
      posts += 1;
      operationId = route.request().headers()["idempotency-key"];
      return route.fulfill({ status: 202, json: { operation_id: operationId, state } });
    });
    await page.route("**/api/operations/*", route => {
      receiptReads += 1;
      expect(new URL(route.request().url()).pathname).toBe(`/api/operations/${operationId}`);
      return route.fulfill({ json: complete ? { operation_id: operationId, state: "complete", response: { repertoire_id: "visual-repertoire", source_name: "active.pgn", games_found: 1, unique_lines: 1, cards_created: 1, duplicates_merged: 0, cards_admitted_today: 0 } } : { operation_id: operationId, state } });
    });
    await navigate(page, "Repertoire");
    await page.getByRole("button", { name: /Import PGN/ }).click();
    await page.locator('input[type="file"]').setInputFiles({ name: "active.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from("1. e4 e5 2. Nf3 *") });
    await expect(page.getByRole("button", { name: "Import repertoire", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Import repertoire", exact: true }).click();
    await expect.poll(() => receiptReads).toBeGreaterThan(0);
    await expect(page.getByRole("button", { name: "Importing…" })).toBeDisabled();
    await expect(page.locator('input[type="file"]')).toBeDisabled();
    await expect(page.getByRole("dialog").getByRole("alert")).toHaveCount(0);
    await page.clock.runFor(30_000);
    await expect(page.getByRole("button", { name: "Check again", exact: true })).toBeVisible();
    await expect(page.getByRole("dialog").getByRole("status")).toContainText("still processing");
    await expect(page.getByRole("dialog").getByRole("alert")).toHaveCount(0);
    expect(posts).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: `test-results/pgn-import-recovery/pending-${state}-320.png` });
    complete = true;
    await page.getByRole("button", { name: "Check again", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Imported", exact: true })).toBeVisible();
    expect(posts).toBe(1);
    expect(await page.evaluate(() => localStorage.getItem("tempo-pending-pgn-import-v1"))).toBeNull();
  });
}
