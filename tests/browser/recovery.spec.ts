import { test, expect, navigate, prepareUI } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";

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
