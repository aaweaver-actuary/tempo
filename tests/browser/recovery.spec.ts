import { test, expect, navigate, prepareUI } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
import { test as productTest, api } from "./product-fixtures";
import { heldDrag, prepareHeldDrag } from "./held-drag-fixtures";
import type { Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { postgresFixtureSqlArguments, verifyPostgresFixtureTarget } from "./product-fixture-reset";
import { expectedPieces, renderedPieces, squareCenter } from "./keyboard-fixtures";

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

for (const unresolvedHead of [false, true]) {
test(unresolvedHead ? "unconfirmed_review_does_not_advance_prepared_card: filtering an issued unresolved head does not enable its successor"
  : "unresolved guided review keeps its card paused while other training opens", async ({ page }) => {
  await prepareVisualUI(page);
  await page.route("**/api/queue/entries/1974/fail", (route) => route.fulfill({
    status: 409, json: { detail: "This queue attempt is no longer active" },
  }));
  await page.route("**/api/cards/pending-card/review", (route) => route.fulfill({
    status: 409, json: { detail: "This queue attempt is no longer available" },
  }));
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    // The retained original entry is no longer available (the 409 above).
    // The authoritative read must issue the independent head itself; filtering
    // an unresolved issued head cannot authorize a prefetched successor.
    count: unresolvedHead ? 2 : 1, cards: [
      ...(unresolvedHead ? [{ id: "pending-card", queue_entry_id: 1974, start_fen: startFen,
        moves: ["e2e4"], content_type: "opening", repertoire_name: "Pending review",
        repertoire_source: "PGN", trained_color: "white" }] : []),
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

  if (unresolvedHead) {
    await expect(page.getByRole("heading", { name: "Local service unavailable" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Available review" })).toHaveCount(0);
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "false");
    await expect(page.getByRole("button", { name: "Check saved reviews" })).toBeVisible();
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]"))).toHaveLength(1);
    return;
  }
  await expect(page.getByRole("heading", { name: "Available review" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Pending review" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Check saved reviews" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Correct" })).toBeEnabled();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]"))).toHaveLength(1);
});
}

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

test("issue36_browser_alternatives_retry_and_confirmed_edit_invalidation_preserve_the_board_and_grading", async ({ page }) => {
  const card = { id: "issue36-card", queue_entry_id: 3601, revision: 1, start_fen: startFen,
    moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening" as const, repertoire_id: "visual-repertoire",
    repertoire_name: "Issue36", repertoire_source: "PGN", first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" as const };
  const alternative = { id: "issue36-alternative", repertoire_id: "visual-repertoire", repertoire_name: "Issue36",
    name: "Alternative", trained_color: "white" as const, start_fen: startFen, moves: ["d2d4"] };
  await prepareVisualUI(page, true, [card], [alternative]);
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  let unavailable = true;
  let lines = [alternative];
  let failureRequests = 0;
  await page.route("**/api/repertoire/lines", route => route.fulfill(unavailable
    ? { status: 503, json: { detail: "Issue36 unavailable evidence" } } : { json: { lines } }));
  await page.route("**/api/queue/entries/3601/fail", route => {
    failureRequests++; return route.fulfill({ json: { attempt_failed: true } });
  });
  await page.route("**/api/cards/issue36-card", route => {
    // A confirmed mutation removes the alternative, and its reload fails.
    unavailable = true; lines = [{ ...alternative, moves: ["e2e4"] }];
    return route.fulfill({ json: { card_id: "issue36-card", revision: 2, replaced: true, history_mode: "preserve" } });
  });
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  const board = page.locator(".board-frame");
  const playAlternative = async () => {
    for (const square of ["d2", "d4"]) {
      const center = await squareCenter(board, square); await page.mouse.click(center.x, center.y);
    }
  };
  await expect(page.getByRole("button", { name: "Retry loading lines" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Issue36", exact: true })).toBeVisible();
  await playAlternative();
  await expect(page.getByText(/Cannot verify another repertoire move until lines load/)).toBeVisible();
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(startFen));
  expect(failureRequests).toBe(0);
  unavailable = false;
  await page.getByRole("button", { name: "Retry loading lines" }).click();
  await expect(page.getByRole("button", { name: "Retry loading lines" })).toHaveCount(0);
  await playAlternative();
  await expect(page.getByText(/Valid repertoire move · follow the arrow/)).toBeVisible();
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(startFen));
  expect(failureRequests).toBe(0);

  await page.getByRole("button", { name: /Edit card/ }).click();
  await page.getByRole("button", { name: "Validate & save" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry loading lines" })).toBeVisible();
  await playAlternative();
  await expect(page.getByText(/Cannot verify another repertoire move until lines load/)).toBeVisible();
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(startFen));
  expect(failureRequests).toBe(0);
  unavailable = false;
  await page.getByRole("button", { name: "Retry loading lines" }).click();
  await expect(page.getByRole("button", { name: "Retry loading lines" })).toHaveCount(0);
  await playAlternative();
  await expect.poll(() => failureRequests).toBe(1);
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(startFen));
  await expect(page.getByText(/Again recorded/)).toBeVisible();
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
          },
          {
            id: "white",
            name: "London System",
            source_name: "white.pgn",
            line_count: 1569,
            card_count: 813,
            due_count: 55,
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

for (const fault of ["malformed", "foreign"] as const) {
  productTest(`${fault} settings receipt retains the original save across reload without duplicate mutation`, async ({ page, request }) => {
    productTest.setTimeout(120_000);
    await prepareUI(page);
    let operationId = "";
    let corrupt = true;
    let corruptReads = 0;
    const postedKeys: string[] = [];
    await page.route("**/api/settings", async route => {
      if (route.request().method() !== "PUT") return route.continue();
      operationId = route.request().headers()["idempotency-key"];
      postedKeys.push(operationId);
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBe(true);
      await expect.poll(async () => (await (await request.get(`${api}/operations/${operationId}`)).json()).state,
        { timeout: 30_000 }).toBe("complete");
      await route.fulfill({ status: 202, json: { operation_id: operationId, state: "pending" } });
    });
    await page.route("**/api/operations/*", async route => {
      const response = await route.fetch();
      const receipt = await response.json();
      if (receipt.operation_id !== operationId) return route.fulfill({ response });
      if (corrupt) {
        corruptReads++;
        return route.fulfill({ response, json: fault === "foreign"
          ? { ...receipt, operation_id: `foreign-${operationId}` }
          : { ...receipt, state: "invalid-state" } });
      }
      // The local settings owner intentionally accepts old receipts without an
      // identity field. The foreign case instead settles with its matching ID.
      if (fault === "malformed") delete receipt.operation_id;
      return route.fulfill({ response, json: receipt });
    });
    await navigate(page, "Settings");
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect.poll(() => corruptReads).toBeGreaterThan(0);
    const original = await page.evaluate(() => localStorage.getItem("tempo-pending-settings-save-v1"));
    expect(JSON.parse(original!)).toMatchObject({ operationId });
    const settingsMessages = () => page.evaluate(() =>
      (JSON.parse(localStorage.getItem("tempo-notifications-v1") ?? "[]") as { source: string; message: string }[])
        .filter(record => record.source === "settings").map(record => record.message));
    await expect.poll(settingsMessages).toContain("Settings save is pending. Retry with the same changes to check it.");
    expect(await settingsMessages()).not.toContain("Saved.");
    await page.reload();
    await navigate(page, "Settings");
    expect(await page.evaluate(() => localStorage.getItem("tempo-pending-settings-save-v1"))).toBe(original);
    const before = await (await request.get(`${api}/operations/${operationId}`)).json();
    corrupt = false;
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect.poll(settingsMessages).toContain("Saved.");
    await page.getByRole("button", { name: "Notifications", exact: true }).click();
    await page.getByRole("region", { name: "Notifications", exact: true }).getByRole("button", { name: "All", exact: true }).click();
    await expect(page.getByRole("region", { name: "Notifications", exact: true }).getByText("Saved.", { exact: true })).toBeVisible();
    expect(postedKeys).toEqual([operationId]);
    expect(await page.evaluate(() => localStorage.getItem("tempo-pending-settings-save-v1"))).toBeNull();
    expect(await (await request.get(`${api}/operations/${operationId}`)).json()).toEqual(before);
  });
}

test("frontend errors show a redacted copyable debug bundle", async ({ page }) => {
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
  await page.evaluate(() => window.dispatchEvent(new ErrorEvent("error", {
    message: "Frontend diagnostic failure",
    error: new Error("Frontend diagnostic failure"),
  })));
  await page.getByRole("button", { name: "Notifications" }).click();
  await expect(page.getByRole("button", { name: "Copy debug info" })).toBeVisible();
  await page.getByRole("button", { name: "Copy debug info" }).click();
  await expect(page.getByRole("button", { name: "Copied debug info" })).toBeVisible();
  const copied = await page.evaluate(
    () => JSON.parse((window as unknown as { __tempoCopiedDebug: string }).__tempoCopiedDebug),
  );
  expect(copied.schemaVersion).toBe(1);
  expect(copied.error.message).toContain("Frontend diagnostic failure");
  expect(copied.workspace.activeView).toBe("train");
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
  await expect(page.getByRole("button", { name: "Check again", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Check again", exact: true }).click();
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

for (const fault of ["malformed", "foreign"] as const) {
  productTest(`${fault} PGN receipt preserves the original import journal until genuine settlement`, async ({ page, request }) => {
    productTest.setTimeout(120_000);
    await prepareUI(page);
    let operationId = "";
    let corrupt = true;
    let corruptReads = 0;
    const postedKeys: string[] = [];
    await page.route("**/api/imports/pgn", async route => {
      operationId = route.request().headers()["idempotency-key"];
      postedKeys.push(operationId);
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBe(true);
      // Preserve the real import. Lose only its immediate acknowledgment.
      await route.fulfill({ status: 202, json: { operation_id: operationId, state: "pending" } });
    });
    await page.route("**/api/operations/*", async route => {
      const response = await route.fetch();
      const receipt = await response.json();
      if (corrupt && receipt.operation_id === operationId && receipt.state === "complete") {
        corruptReads++;
        await route.fulfill({ response, json: fault === "foreign"
          ? { ...receipt, operation_id: `foreign-${operationId}` }
          : { ...receipt, state: "invalid-state" } });
      } else await route.fulfill({ response });
    });
    await page.getByRole("button", { name: "Repertoire", exact: true }).click();
    await page.getByRole("button", { name: /Import PGN/ }).click();
    const file = { name: `receipt-${fault}.pgn`, mimeType: "application/x-chess-pgn",
      buffer: Buffer.from('[Event "Receipt recovery"]\n\n1. e4 e5 2. Nf3 *') };
    await page.locator('input[type="file"]').setInputFiles(file);
    await page.getByRole("button", { name: "Import repertoire", exact: true }).click();
    await expect.poll(() => corruptReads).toBeGreaterThan(0);
    const dialog = page.getByRole("dialog");
    // Observe the consumer's rejection, not merely delivery of a corrupt response.
    if (fault === "foreign") await expect(dialog.getByRole("alert")).toHaveText(
      "The service returned a different import operation identity. Check the local service before retrying.");
    else await expect(dialog.getByRole("status")).toContainText("Import confirmation is unavailable");
    const commandConfirmation = () => dialog.getByText("Import confirmed", { exact: true })
      .or(dialog.getByRole("heading", { name: /^(Imported|PGN saved)$/ }));
    await expect(commandConfirmation()).toHaveCount(0);
    const original = await page.evaluate(() => localStorage.getItem("tempo-pending-pgn-import-v1"));
    expect(JSON.parse(original!)).toMatchObject({ operationId });
    await expect(page.getByRole("heading", { name: "Imported", exact: true })).toHaveCount(0);
    await page.reload();
    await page.getByRole("button", { name: "Repertoire", exact: true }).click();
    await page.getByRole("button", { name: /Import PGN/ }).click();
    await page.locator('input[type="file"]').setInputFiles(file);
    expect(await page.evaluate(() => localStorage.getItem("tempo-pending-pgn-import-v1"))).toBe(original);
    const actualReceipt = await (await request.get(`${api}/operations/${operationId}`)).json();
    expect(actualReceipt).toMatchObject({ operation_id: operationId, state: "complete" });
    corrupt = false;
    await page.getByRole("button", { name: "Check again", exact: true }).click();
    // Command confirmation precedes graph/queue publication. A truthful
    // "Import confirmed" while preparation runs is not a claim of ready cards.
    await expect(commandConfirmation()).toBeVisible();
    expect(postedKeys).toEqual([operationId]);
    expect(await page.evaluate(() => localStorage.getItem("tempo-pending-pgn-import-v1"))).toBeNull();
    expect(await (await request.get(`${api}/operations/${operationId}`)).json()).toEqual(actualReceipt);
    const repertoires = (await (await request.get(`${api}/repertoires`)).json()).repertoires;
    expect(repertoires.filter((item: { id: string }) => item.id === actualReceipt.response.repertoire_id)).toHaveLength(1);
  });
}

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
  await expect(page.getByRole("button", { name: "Check again", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Check again", exact: true }).click();
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

test("discarding an unknown PGN import permits a different file after reload", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await prepareVisualUI(page);
  await preparePgnReceiptCompletion(page);
  const oldOperationId = "discard-original-import";
  await page.evaluate(identity => localStorage.setItem("tempo-pending-pgn-import-v1", JSON.stringify({ operationId: identity, fingerprint: "lost.pgn:white:6:digest" })), oldOperationId);
  let discarded = false;
  const submittedKeys: string[] = [];
  await page.route("**/api/imports/pgn/*/discard", async route => {
    expect(route.request().method()).toBe("POST");
    expect(new URL(route.request().url()).pathname).toBe(`/api/imports/pgn/${oldOperationId}/discard`);
    discarded = true;
    await route.fulfill({ json: { operation_id: oldOperationId, outcome: "discarded" } });
  });
  await page.route("**/api/operations/*", route => route.fulfill({ json: {
    operation_id: oldOperationId, state: discarded ? "failed" : "unknown",
    ...(discarded ? { error: { code: "import_discarded" } } : {}),
  } }));
  await page.route("**/api/imports/pgn", route => {
    submittedKeys.push(route.request().headers()["idempotency-key"]);
    return route.fulfill({ json: { repertoire_id: "visual-repertoire", source_name: "different.pgn", games_found: 1, unique_lines: 1, cards_created: 1, duplicates_merged: 0, cards_admitted_today: 0 } });
  });
  await page.reload();
  await navigate(page, "Repertoire");
  await page.getByRole("button", { name: /Import PGN/ }).click();
  await expect(page.locator('input[type="file"]')).toHaveValue("");
  await page.getByRole("button", { name: "Discard pending import", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText("Pending import discarded");
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-pgn-import-v1"))).toBeNull();
  expect(submittedKeys).toEqual([]);
  await page.locator('input[type="file"]').setInputFiles({ name: "different.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from("1. d4 d5 2. Bf4 *") });
  await page.getByRole("button", { name: "Import repertoire", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Imported", exact: true })).toBeVisible();
  expect(submittedKeys).toHaveLength(1);
  expect(submittedKeys[0]).not.toBe(oldOperationId);
});

for (const receiptFault of ["none", "malformed", "foreign"] as const) {
productTest(receiptFault === "none" ? "repair choices advance before a delayed save and survive reload through real PostgreSQL receipts"
  : `${receiptFault} integrity receipts preserve original repair and retry journals through real PostgreSQL recovery`, async ({ page, request }) => {
  // Allow setup plus the full eventual-recovery poll below.
  productTest.setTimeout(240_000);
  await prepareUI(page);
  // Await analysis from the retained passive Repertoire view so the active Train
  // workspace does not intentionally preempt the integrity slices this receipt test needs.
  await page.getByRole("button", { name: "Repertoire", exact: true }).click();
  const repairPgn = '[Event "First e4"]\n\n1. e4 *\n\n[Event "First d4"]\n\n1. d4 *\n\n' +
    '[Event "Second e4"]\n[SetUp "1"]\n[FEN "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2"]\n\n2. e4 *\n\n' +
    '[Event "Second d4"]\n[SetUp "1"]\n[FEN "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2"]\n\n2. d4 *';
  const conflict = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: "blocked-repair.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from(repairPgn) }, initial_depth: "1",
  } });
  expect(conflict.status()).toBe(409);
  expect((await conflict.json()).detail.code).toBe("repertoire_conflict");
  expect((await (await request.get(`${api}/repertoires`)).json()).repertoires
    .some((item: { name: string }) => item.name === "blocked-repair")).toBe(false);

  // Seed a pre-existing conflict as old persisted authored content, never via
  // the current import path, which correctly rejects competing answers.
  const cleanPgn = '[Event "First e4"]\n\n1. e4 *\n\n' +
    '[Event "Second e4"]\n[SetUp "1"]\n[FEN "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2"]\n\n2. e4 *';
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: "queued-repair.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from(cleanPgn) }, initial_depth: "1",
  } });
  expect(imported.ok(), await imported.text()).toBe(true);
  let repertoireId = "";
  let seedGraph: { id: string; generation: number } | undefined;
  await expect.poll(async () => {
    const result = await (await request.get(`${api}/repertoires`, { headers: { "X-Tempo-Work-Class": "background" } })).json();
    const repertoire = result.repertoires.find((item: { name: string }) => item.name === "queued-repair");
    repertoireId = repertoire?.id ?? "";
    const tasks = (await (await request.get(`${api}/system/tasks`)).json()).tasks;
    seedGraph = tasks.find((task: { kind: string; deduplication_key: string; state: string }) =>
      task.kind === "opening_graph_rebuild" && task.deduplication_key === repertoireId && task.state === "complete");
    return Boolean(seedGraph && repertoire?.integrity_scan_status === "idle");
  }, { timeout: 60_000 }).toBe(true);
  expect(repertoireId).toMatch(/^[a-zA-Z0-9_-]+$/);
  expect(seedGraph!.id).toMatch(/^[a-zA-Z0-9_-]+$/);
  const health = await (await request.get(`${api}/health`)).json();
  const postgresId = await verifyPostgresFixtureTarget({ health, apiUrl: api, project: process.env.TEMPO_TEST_COMPOSE_PROJECT });
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  expect(day).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  const seedSql = `INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
    SELECT 'legacy-' || id,repertoire_id,'Synthetic legacy competing line',trained_color,start_fen,'["d2d4"]',created_at
    FROM repertoire_lines WHERE repertoire_id='${repertoireId}' ORDER BY id LIMIT 1;
    UPDATE background_tasks SET state='failed',phase='failed',generation=generation+1,
      payload_json=json_build_object('repertoire_id','${repertoireId}','local_day','${day}')::text,
      last_error='synthetic legacy fixture' WHERE id='${seedGraph!.id}';`;
  execFileSync("docker", postgresFixtureSqlArguments(postgresId, seedSql, 5000), { encoding: "utf8", timeout: 10_000 });
  const seedRetryId = `seed-retry-${repertoireId}`;
  expect((await request.post(`${api}/system/tasks/${seedGraph!.id}/retry`, { headers: { "Idempotency-Key": seedRetryId } })).ok()).toBe(true);
  await expect.poll(async () => {
    const result = await (await request.get(`${api}/repertoires`, { headers: { "X-Tempo-Work-Class": "background" } })).json();
    const repertoire = result.repertoires.find((item: { id: string }) => item.id === repertoireId);
    return { integrity_issue_count: repertoire?.integrity_issue_count,
      integrity_status: repertoire?.integrity_status, integrity_scan_status: repertoire?.integrity_scan_status };
  }, { timeout: 60_000 }).toEqual({ integrity_issue_count: 1, integrity_status: "needs_repair", integrity_scan_status: "idle" });
  let releaseSave: (() => void) | undefined;
  const saveHold = new Promise<void>(resolve => { releaseSave = resolve; });
  let savedOnServer = false;
  let corruptReceipts = receiptFault !== "none";
  let corruptedReads = 0;
  let corruptedOperationId = "";
  await page.route("**/api/operations/*", async route => {
    const response = await route.fetch();
    const receipt = await response.json();
    if (corruptReceipts && receipt.operation_id === corruptedOperationId && receipt.state === "complete") {
      corruptedReads++;
      await route.fulfill({ response, json: receiptFault === "foreign"
        ? { ...receipt, operation_id: `foreign-${receipt.operation_id}` }
        : { ...receipt, state: "not-a-receipt-state" } });
    } else await route.fulfill({ response });
  });
  const submittedKeys: string[] = [];
  await page.route("**/integrity/issues/*/resolve", async route => {
    submittedKeys.push(route.request().headers()["idempotency-key"]);
    corruptedOperationId = submittedKeys.at(-1)!;
    const response = await route.fetch();
    if (receiptFault !== "none") {
      const operationId = submittedKeys.at(-1)!;
      await expect.poll(async () => (await (await request.get(`${api}/operations/${operationId}`)).json()).state,
        { timeout: 60_000 }).toBe("complete");
    }
    savedOnServer = true;
    if (submittedKeys.length === 1) await saveHold;
    await route.fulfill({ response }).catch(() => { /* Reload intentionally loses the first response. */ });
  });
  // Wait for the consumer's durable rejection, not merely route delivery. A
  // retained record alone could already have advanced incorrectly to validation.
  const assertRejectedWithoutAdvancing = async (original: Record<string, unknown>) => {
    const operationId = String(original.operationId);
    const readSaved = () => page.evaluate(id => {
      const raw = localStorage.getItem(`tempo-pending-integrity-repairs-v3:${id}`);
      return raw === null ? null : JSON.parse(raw);
    }, operationId);
    const rejection = receiptFault === "foreign"
      ? "The repair receipt has an unrelated identity." : "Invalid repair receipt data.";
    await expect.poll(async () => (await readSaved())?.error).toBe(rejection);
    const rejected = await readSaved();
    expect(rejected.attempts).toBeGreaterThan(Number(original.attempts ?? 0));
    expect(rejected.nextAttemptAt).toBeGreaterThan(0);
    // Only retry accounting/error may advance. Choice, original operation,
    // validation task and pending retry authority must all remain unchanged.
    for (const key of ["operationId", "repertoireId", "issueId", "signature", "selectedMoveUci",
      "queuedAt", "phase", "taskId", "taskGeneration", "retryTaskId", "retryOperationId", "retry"])
      expect(rejected[key], `Rejected receipt changed ${key}`).toEqual(original[key]);
  };
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "Repertoire", exact: true }).click();
    await page.getByRole("button", { name: "Resume repair", exact: true }).click({ timeout: 15_000 });
    const dialog = page.getByRole("dialog", { name: "Choose one response per position" });
    await expect(dialog).toContainText("1 issue remaining");
    await dialog.getByRole("button", { name: "Choose e4", exact: true }).click({ timeout: 15_000 });
    await dialog.getByRole("button", { name: "Keep this response", exact: true }).click();
    await expect(dialog).toContainText("All choices queued");
    await expect.poll(() => savedOnServer).toBe(true);
    expect(submittedKeys).toHaveLength(1);
    const savedChoices = await page.evaluate(() => Object.keys(localStorage)
      .filter(key => key.startsWith("tempo-pending-integrity-repairs-v3:"))
      .map(key => JSON.parse(localStorage.getItem(key)!)));
    expect(savedChoices).toHaveLength(1);
    await page.reload(); releaseSave?.();
    if (receiptFault !== "none") {
      await expect.poll(() => corruptedReads).toBeGreaterThan(0);
      await assertRejectedWithoutAdvancing(savedChoices[0]);
      expect(submittedKeys).toHaveLength(1);
      corruptReceipts = false;
      await page.reload();
    }
    await expect(page.getByRole("dialog", { name: "Choose one response per position" })).toHaveCount(0);
    // Prove eventual recovery through graph and integrity publication, not a
    // 20-second performance SLO for the asynchronous background pipeline.
    await expect.poll(async () => page.evaluate(() => Object.keys(localStorage)
      .filter(key => key.startsWith("tempo-pending-integrity-repairs-v3:")).length), { timeout: 60_000 }).toBe(0);
    expect(submittedKeys).toHaveLength(1);
    expect(submittedKeys.sort()).toEqual(savedChoices.map(choice => choice.operationId).sort());
    const integrity = await (await request.get(`${api}/repertoires/${repertoireId}/integrity`)).json();
    expect(integrity).toMatchObject({ status: "clean", scan_status: "idle", issue_count: 0 });
    for (const operationId of submittedKeys) {
      const receipt = await (await request.get(`${api}/operations/${operationId}`)).json();
      expect(receipt.state).toBe("complete");
    }

    // Reopen a real completed graph task as terminal in this verified disposable
    // fixture; retain the accepted repair's original operation identity.
    const tasks = (await (await request.get(`${api}/system/tasks`)).json()).tasks;
    const graph = tasks.find((task: { kind: string; deduplication_key: string; state: string }) =>
      task.kind === "opening_graph_rebuild" && task.deduplication_key === repertoireId && task.state === "complete");
    expect(graph).toBeTruthy();
    expect(graph.id).toMatch(/^[a-zA-Z0-9_-]+$/);
    const retiredId = `retired-${graph.id}`;
    const sql = `UPDATE background_tasks SET state='failed', phase='failed', last_error='synthetic retry witness' WHERE id='${graph.id}';
      INSERT INTO background_tasks(id,kind,deduplication_key,state,phase,payload_json,next_attempt_at,created_at,updated_at)
      VALUES ('${retiredId}','game_analysis_followup','${retiredId}','failed','failed','{}',now()::text,now()::text,now()::text);`;
    execFileSync("docker", postgresFixtureSqlArguments(postgresId, sql, 5000), { encoding: "utf8", timeout: 10_000 });
    expect((await request.post(`${api}/system/tasks/${retiredId}/retry`, { headers: { "Idempotency-Key": `retired-${submittedKeys[0]}` } })).status()).toBe(404);
    const terminal = (await (await request.get(`${api}/system/tasks`)).json()).tasks.find((task: { id: string }) => task.id === graph.id);
    expect(terminal).toMatchObject({ state: "failed", generation: graph.generation });
    await page.evaluate(({ repair, graphTask }) => {
      const restored = { ...repair, phase: "validating", taskId: graphTask.id,
        taskGeneration: graphTask.generation, retry: undefined, retryTaskId: undefined, retryOperationId: undefined };
      localStorage.setItem(`tempo-pending-integrity-repairs-v3:${repair.operationId}`, JSON.stringify(restored));
    }, { repair: savedChoices[0], graphTask: graph });
    let retryId = "";
    let pendingRetry: Record<string, unknown> | undefined;
    if (receiptFault !== "none") {
      corruptReceipts = true;
      corruptedReads = 0;
      await page.route(`**/api/system/tasks/${graph.id}/retry`, async route => {
        corruptedOperationId = route.request().headers()["idempotency-key"];
        pendingRetry = await page.evaluate(id => JSON.parse(localStorage.getItem(
          `tempo-pending-integrity-repairs-v3:${id}`)!), savedChoices[0].operationId);
        expect(pendingRetry).toMatchObject({ retryOperationId: corruptedOperationId,
          retry: { kind: "task", taskId: graph.id, taskResetConfirmed: false } });
        const response = await route.fetch();
        expect(response.ok()).toBe(true);
        const originalRetryId = route.request().headers()["idempotency-key"];
        await expect.poll(async () => (await (await request.get(`${api}/operations/${originalRetryId}`)).json()).state,
          { timeout: 60_000 }).toBe("complete");
        // The mutation is genuine; only its acknowledgment is delayed so that
        // the browser must inspect the original durable operation receipt.
        await route.fulfill({ status: 202, json: {
          operation_id: route.request().headers()["idempotency-key"], state: "pending",
        } });
      });
    }
    page.on("request", requestEvent => {
      if (requestEvent.method() === "POST" && new URL(requestEvent.url()).pathname === `/api/system/tasks/${graph.id}/retry`)
        retryId = requestEvent.headers()["idempotency-key"] ?? "";
    });
    await page.reload();
    await page.locator(".integrity-repair-status").getByRole("button", { name: "Retry repair", exact: true }).click();
    await expect.poll(() => retryId).not.toBe("");
    await expect.poll(async () => (await (await request.get(`${api}/operations/${retryId}`)).json()).state,
      { timeout: 60_000 }).toBe("complete");
    const receiptBefore = await (await request.get(`${api}/operations/${retryId}`)).json();
    if (receiptFault !== "none") {
      await expect.poll(() => corruptedReads).toBeGreaterThan(0);
      expect(pendingRetry).toBeDefined();
      expect(pendingRetry!.retryOperationId).toBe(retryId);
      await assertRejectedWithoutAdvancing(pendingRetry!);
      corruptReceipts = false;
      await page.reload();
    }
    const replay = await request.post(`${api}/system/tasks/${graph.id}/retry`, { headers: { "Idempotency-Key": retryId } });
    expect(replay.ok()).toBe(true);
    expect(await (await request.get(`${api}/operations/${retryId}`)).json()).toMatchObject({
      operation_id: retryId, state: "complete", response: receiptBefore.response,
    });
    await expect.poll(async () => page.evaluate(id => localStorage.getItem(`tempo-pending-integrity-repairs-v3:${id}`), savedChoices[0].operationId),
      { timeout: 60_000 }).toBeNull();
    const recovered = (await (await request.get(`${api}/system/tasks`)).json()).tasks.find((task: { id: string }) => task.id === graph.id);
    expect(recovered).toMatchObject({ state: "complete", generation: graph.generation });
  } finally { releaseSave?.(); }
});
}

test("repair confirmation during a held training piece preserves the drag and active attempt", async ({ page }, testInfo) => {
  let releaseIntegrity: (() => void) | undefined;
  let validationStarted = false;
  let validationCompleted = false;
  const integrityHold = new Promise<void>(resolve => { releaseIntegrity = resolve; });
  await prepareHeldDrag(page, true, async () => {
    await page.addInitScript(() => localStorage.setItem("tempo-pending-integrity-repairs-v3:held-repair", JSON.stringify({
      repertoireId: "visual-repertoire", issueId: "held-issue", signature: "held-signature", selectedMoveUci: "e2e4",
      operationId: "held-repair", phase: "validating", taskId: "held-graph", queuedAt: 1,
    })));
    await page.route("**/api/system/tasks", route => route.fulfill({ json: { tasks: [{ id: "held-graph", kind: "opening_graph_rebuild",
      deduplication_key: "visual-repertoire", generation: 1, state: "complete", last_error: null }] } }));
    await page.route("**/api/repertoires", route => route.fulfill({ json: { repertoires: [{ id: "visual-repertoire", name: "Spanish opening",
      source_name: "fixture.pgn", line_count: 1, card_count: 1, due_count: 1, graph_state: "ready", graph_generation: 1,
      integrity_status: "clean" }] } }));
    await page.route("**/api/repertoires/*/integrity", async route => {
      validationStarted = true; await integrityHold;
      await route.fulfill({ json: { repertoire_id: "visual-repertoire", status: "clean", issue_count: 0, first_issue_id: null,
        scan_status: "idle", scan_generation: "held-scan:1", scan_progress: { completed: 1, total: 1 }, last_scan_error: null, issues: [] } });
      validationCompleted = true;
    });
  });
  try {
    await expect.poll(() => validationStarted).toBe(true);
    const fenBefore = await page.locator(".board-frame").getAttribute("data-fen");
    const result = await heldDrag(page, async step => {
      if (step === 10) releaseIntegrity?.();
      if (step === 25) await expect.poll(async () => page.evaluate(() => localStorage.getItem("tempo-pending-integrity-repairs-v3:held-repair"))).toBeNull();
    }, false);
    expect(validationCompleted).toBe(true); expect(result.probe.sawDragging).toBe(true); expect(result.probe.interrupted).toBe(false);
    await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", fenBefore!);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await testInfo.attach("held-repair-confirmation", { body: JSON.stringify(result), contentType: "application/json" });
  } finally { releaseIntegrity?.(); await page.mouse.up(); }
});

test("asynchronous repair validation retry survives reload without repeating the retry command", async ({ page }) => {
  await prepareVisualUI(page);
  await page.addInitScript(() => {
    if (!localStorage.getItem("tempo-repair-retry-fixture-initialized")) {
      localStorage.setItem("tempo-repair-retry-fixture-initialized", "true");
      localStorage.setItem("tempo-pending-integrity-repairs-v3:retry-repair", JSON.stringify({
        repertoireId: "visual-repertoire", issueId: "retry-issue", signature: "retry-signature", selectedMoveUci: "e2e4",
        operationId: "retry-repair", phase: "validating", taskId: "retry-graph", queuedAt: 1,
      }));
    }
  });
  let taskComplete = false;
  let retryOperationId = "";
  let retryPosts = 0;
  let retryReads = 0;
  await page.route("**/api/system/tasks", route => route.fulfill({ json: { tasks: [{ id: "retry-graph", kind: "opening_graph_rebuild",
    deduplication_key: "visual-repertoire", generation: 1, state: taskComplete ? "complete" : "failed", last_error: "Worker needs retry" }] } }));
  await page.route("**/api/system/tasks/retry-graph/retry", route => {
    retryPosts += 1; retryOperationId = route.request().headers()["idempotency-key"];
    return route.fulfill({ status: 202, json: { operation_id: retryOperationId, state: "pending" } });
  });
  await page.route("**/api/operations/*", route => {
    const operationId = new URL(route.request().url()).pathname.split("/").at(-1);
    if (operationId === retryOperationId) {
      retryReads += 1;
      return route.fulfill({ json: { state: taskComplete ? "complete" : "pending", operation_id: retryOperationId,
        ...(taskComplete ? { response: { id: "retry-graph" } } : {}) } });
    }
    return route.fulfill({ json: { state: "unknown" } });
  });
  await page.route("**/api/repertoires", route => route.fulfill({ json: { repertoires: [{ id: "visual-repertoire", name: "Spanish opening",
    source_name: "fixture.pgn", line_count: 1, card_count: 1, due_count: 1, graph_state: "ready", graph_generation: 1, integrity_status: "clean" }] } }));
  await page.route("**/api/repertoires/*/integrity", route => route.fulfill({ json: { repertoire_id: "visual-repertoire", status: "clean",
    issue_count: 0, first_issue_id: null, scan_status: "idle", scan_generation: "retry-scan:1",
    scan_progress: { completed: 1, total: 1 }, last_scan_error: null, issues: [] } }));
  await page.goto("/");
  await page.locator(".integrity-repair-status").getByRole("button", { name: "Retry repair", exact: true }).click();
  await expect.poll(() => retryPosts).toBe(1);
  await expect.poll(() => retryReads).toBeGreaterThan(0);
  await page.reload();
  await expect.poll(() => retryReads).toBeGreaterThan(1);
  await expect(page.locator(".integrity-repair-status")).toContainText("Validating");
  await expect(page.locator(".integrity-repair-status").getByRole("button", { name: "Retry repair", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-integrity-repairs-v3:retry-repair")!).retryOperationId)).toBe(retryOperationId);
  taskComplete = true;
  await expect.poll(async () => page.evaluate(() => localStorage.getItem("tempo-pending-integrity-repairs-v3:retry-repair")), { timeout: 15_000 }).toBeNull();
  expect(retryPosts).toBe(1);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
