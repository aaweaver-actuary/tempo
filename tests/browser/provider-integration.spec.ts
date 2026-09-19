import { test, expect, api, nav, boardVisible } from "./product-fixtures";
test("Docker Games shows actual empty records and actionable sync errors, never sample success", async ({
  page,
}) => {
  await page.goto("/");
  await nav(page, "Games");
  await expect(
    page.getByText("No games imported", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/private Site|comparison preview|Sample comparisons/),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "↻ Sync games" }).click();
  await expect(page.getByRole("alert")).toContainText(/username|account/i);
});

test("automatic game sync has a visible spinner and reports provider failure", async ({
  page,
  request,
}) => {
  const settings = await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`, {
    data: { ...settings, lichess_username: "missing-user" },
  });
  await page.route("**/api/games/sync", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.fulfill({
      status: 404,
      json: { detail: "Lichess username not found" },
    });
  });
  await page.goto("/");
  await nav(page, "Games");
  await expect(
    page.getByRole("button", { name: "Syncing games" }),
  ).toBeDisabled();
  await expect(page.locator(".sync-button i")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText(
    "Lichess username not found",
  );
});

test("production Stockfish returns playable engine moves without clipping the board", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem("tempo-stockfish-on", "true");
    localStorage.setItem("tempo-maia-on", "false");
  });
  await page.goto("/");
  await nav(page, "Builder");
  await expect(
    page.locator(".engine-panel .candidate-list button").first(),
  ).toBeVisible({ timeout: 45_000 });
  await boardVisible(page);
});

test("Maia initializes matching runtime assets and returns legal playable probabilities", async ({
  page,
}) => {
  const runtime = await page.request.get("/ort/ort-wasm-simd-threaded.mjs");
  expect(runtime.ok()).toBeTruthy();
  expect(runtime.headers()["content-type"]).toMatch(/javascript/);
  await page.addInitScript(() => {
    localStorage.setItem("tempo-stockfish-on", "false");
    localStorage.setItem("tempo-maia-on", "true");
  });
  await page.goto("/");
  await nav(page, "Builder");
  const panel = page.locator(".analysis-panel").filter({ hasText: "Maia 3" });
  const candidate = panel.locator(".candidate-list button").first();
  await expect(candidate).toBeVisible({ timeout: 45_000 });
  await expect(candidate.locator("small")).toContainText(/\d+%/);
  const startingFen = await page
    .locator(".board-frame")
    .getAttribute("data-fen");
  await candidate.click();
  await expect(page.locator(".board-frame")).not.toHaveAttribute(
    "data-fen",
    startingFen!,
  );
  await boardVisible(page);
});

test("real packaged tactics and standard chess sounds are readable and preloaded before opening Tactics", async ({
  page,
  request,
}) => {
  const catalog = await request.get(
    "/data/tactics-packs/hangingPiece-easy-01.json",
  );
  expect(catalog.ok()).toBeTruthy();
  const deck = (await catalog.json()).filter(
    (record: { DeckId: string }) => record.DeckId === "hangingPiece-easy-01",
  );
  expect(deck).toHaveLength(25);
  for (const path of ["Move", "Capture"])
    expect(
      (await request.get(`/sounds/standard/${path}.mp3`)).ok(),
    ).toBeTruthy();
  let requests = 0;
  const progress = await (await request.get(`${api}/tactics/progress`)).json();
  const discovered = new Set(
    progress["hangingPiece-easy-01"]?.discoveredIds ??
      progress["hangingPiece-easy-01"]?.cleanIds ??
      [],
  );
  const expectedPuzzle = deck.find(
    (record: { PuzzleId: string }) =>
      !discovered.has(`lichess-${record.PuzzleId}`),
  ).DeckPosition;
  page.on("request", (request) => {
    if (request.url().includes("/data/tactics-packs/hangingPiece-easy-01.json"))
      requests++;
  });
  await page.goto("/");
  await expect.poll(() => requests).toBe(1);
  await nav(page, "Tactics");
  await expect(page.locator(".board-frame")).toBeVisible();
  await expect(page.getByText(`Puzzle ${expectedPuzzle} of 25`)).toBeVisible();
  expect(requests).toBe(1);
  await boardVisible(page);
});
