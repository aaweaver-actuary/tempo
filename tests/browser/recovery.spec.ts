import { test, expect, navigate, prepareUI } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";

const startFen =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

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
  await page.route("**/api/queue/today", (route) =>
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
