import { test, expect, type Page } from "@playwright/test";
import { Chess } from "chess.js";

const api = process.env.TEMPO_DOCKER_URL
  ? `${process.env.TEMPO_DOCKER_URL}/api`
  : "http://127.0.0.1:8001/api";
const pgn =
  '[Event "My repertoire"]\n\n1. e4 e5 2. Nf3 Nc6 (2... Nf6) *\n\n[Event "Other line"]\n\n1. d4 d5 2. c4 e6 *';

async function nav(page: Page, name: string) {
  await page
    .getByRole("navigation")
    .getByRole("button", { name, exact: true })
    .click();
}
async function assertSharedBoardShellLayout(page: Page, mobile: boolean) {
  const shell = page.locator(".unified-board-shell-layout").first();
  await expect(shell).toBeVisible();
  const boardRegion = shell.locator(".persistent-board-shell").first();
  const panelRegion = shell.locator(".unified-board-shell-panel").first();
  await expect(boardRegion).toBeVisible();
  await expect(panelRegion).toBeVisible();
  const [boardBox, panelBox] = await Promise.all([
    boardRegion.boundingBox(),
    panelRegion.boundingBox(),
  ]);
  expect(boardBox).not.toBeNull();
  expect(panelBox).not.toBeNull();
  if (!boardBox || !panelBox) return;
  if (mobile)
    expect(boardBox.y + boardBox.height).toBeLessThanOrEqual(panelBox.y + 2);
  else expect(boardBox.x + boardBox.width).toBeLessThanOrEqual(panelBox.x + 2);
}
async function boardVisible(
  page: Page,
  options: { requireControlsInViewport?: boolean } = {},
) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  const board = page.locator(".board-frame").first();
  await expect(board).toBeVisible();
  await expect(board.locator("piece.anim")).toHaveCount(0);
  const box = await board.boundingBox();
  const viewport = page.viewportSize()!;
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(65);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 1);
  expect(Math.abs(box!.width - box!.height)).toBeLessThan(2);
  const surface = await board.locator(".cg-wrap").boundingBox();
  const squares = await board.locator("cg-board").boundingBox();
  expect(surface).not.toBeNull();
  expect(squares).not.toBeNull();
  for (const axis of ["x", "y", "width", "height"] as const)
    expect(Math.abs(surface![axis] - squares![axis])).toBeLessThan(0.6);
  expect(Math.abs(surface!.width - surface!.height)).toBeLessThan(0.6);
  // A Black prompt may start its automatic reply between separate DOM reads.
  // Sample surface and pieces together, and assert the settled geometry rather
  // than an interpolated animation frame.
  await expect
    .poll(() =>
      board.locator(".cg-wrap").evaluate((node) => {
        const bounds = node.getBoundingClientRect();
        return [...node.querySelectorAll("cg-board piece:not(.ghost)")].every(
          (piece) => {
            const b = piece.getBoundingClientRect();
            const file = (b.x - bounds.x) / (bounds.width / 8),
              rank = (b.y - bounds.y) / (bounds.height / 8);
            return (
              !piece.classList.contains("anim") &&
              Math.abs(b.width - bounds.width / 8) < 0.6 &&
              Math.abs(b.height - bounds.height / 8) < 0.6 &&
              Math.abs(file - Math.round(file)) < 0.02 &&
              Math.abs(rank - Math.round(rank)) < 0.02 &&
              file >= -0.02 &&
              file <= 7.02 &&
              rank >= -0.02 &&
              rank <= 7.02
            );
          },
        );
      }),
    )
    .toBe(true);
  const controls = page.locator(".board-tools").first();
  if ((options.requireControlsInViewport ?? true) && (await controls.count())) {
    const controlsBox = (await controls.boundingBox())!;
    expect(controlsBox.y + controlsBox.height).toBeLessThanOrEqual(
      viewport.height + 1,
    );
  }
}

test("high-DPI board geometry stays aligned through narrow resize and orientation flips", async ({
  browser,
}) => {
  const context = await browser.newContext({
    deviceScaleFactor: 2,
    viewport: { width: 1280, height: 800 },
    baseURL: process.env.TEMPO_DOCKER_URL ?? "http://127.0.0.1:3001",
  });
  const page = await context.newPage();
  await page.addInitScript(() => {
    localStorage.setItem("tempo-stockfish-on", "false");
    localStorage.setItem("tempo-maia-on", "false");
  });
  await page.goto("/");
  await nav(page, "Builder");
  for (const viewport of [
    { width: 1280, height: 800 },
    { width: 390, height: 844 },
    { width: 768, height: 600 },
  ]) {
    await page.setViewportSize(viewport);
    await boardVisible(page);
    const orientation = await page
      .locator(".board-frame")
      .getAttribute("data-orientation");
    await page.keyboard.press("f");
    await expect(page.locator(".board-frame")).not.toHaveAttribute(
      "data-orientation",
      orientation!,
    );
    await boardVisible(page);
  }
  await context.close();
});

test("shared board shell keeps board region fixed left on desktop and top on mobile across board workspaces", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem("tempo-stockfish-on", "false");
    localStorage.setItem("tempo-maia-on", "false");
  });
  await page.goto("/");
  for (const view of ["Train", "Builder", "Games", "Tactics", "Endgames"]) {
    await nav(page, view);
    await page.evaluate(() => window.scrollTo(0, 0));
    await boardVisible(page, { requireControlsInViewport: false });
    await assertSharedBoardShellLayout(page, false);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  for (const view of ["Train", "Builder", "Games", "Tactics", "Endgames"]) {
    await nav(page, view);
    await page.evaluate(() => window.scrollTo(0, 0));
    await boardVisible(page, { requireControlsInViewport: false });
    await assertSharedBoardShellLayout(page, true);
  }
});
async function move(page: Page, from: string, to: string) {
  const board = page.locator(".board-frame").first();
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  await boardVisible(page);
  const black = (await board.getAttribute("data-orientation")) === "black";
  for (const square of [from, to]) {
    const box = (await board.locator(".cg-wrap").boundingBox())!;
    const file = square.charCodeAt(0) - 97,
      rank = Number(square[1]) - 1;
    await page.mouse.click(
      box.x + (((black ? 7 - file : file) + 0.5) * box.width) / 8,
      box.y + (((black ? rank : 7 - rank) + 0.5) * box.height) / 8,
    );
  }
}

async function clickSquare(
  page: Page,
  square: string,
  button: "left" | "right" = "left",
) {
  const board = page.locator(".board-frame").first();
  await boardVisible(page);
  const black = (await board.getAttribute("data-orientation")) === "black";
  const box = (await board.locator(".cg-wrap").boundingBox())!;
  const file = square.charCodeAt(0) - 97;
  const rank = Number(square[1]) - 1;
  const x = box.x + (((black ? 7 - file : file) + 0.5) * box.width) / 8;
  const y = box.y + (((black ? rank : 7 - rank) + 0.5) * box.height) / 8;
  await page.mouse.move(x, y);
  await page.mouse.down({ button });
  await page.mouse.up({ button });
}

test.beforeEach(async ({ request }) => {
  const repertoires = (await (await request.get(`${api}/repertoires`)).json())
    .repertoires;
  for (const item of repertoires)
    await request.delete(`${api}/repertoires/${item.id}`);
  const queue = (await (await request.get(`${api}/queue/today`)).json()).cards;
  for (const card of queue)
    if (card.content_type !== "opening")
      await request.delete(`${api}/cards/${card.id}`);
  const settings = await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`, {
    data: {
      ...settings,
      new_cards_per_day: 2,
      initial_depth: 2,
      lichess_username: "",
      chesscom_username: "",
    },
  });
});

test("local import respects the daily limit; Black prompts and Builder flip survive Settings and refresh", async ({
  page,
}) => {
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "mine.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(pgn),
  });
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Black", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Import repertoire", exact: true })
    .click();
  await expect(page.getByText(/2 cards are in today’s queue/)).toBeVisible();
  await page.getByRole("button", { name: "View imported repertoire" }).click();
  await nav(page, "Train");
  await expect(page.locator(".session-count strong")).toHaveText("2");
  await expect
    .poll(async () =>
      new Chess(
        (await page.locator(".board-frame").getAttribute("data-fen"))!,
      ).turn(),
    )
    .toBe("b");
  await expect(page.locator(".board-frame")).toHaveAttribute(
    "data-input-enabled",
    "true",
  );
  await boardVisible(page);
  await nav(page, "Builder");
  const selector = page.getByRole("combobox", { name: "Active repertoire" });
  await expect(selector).not.toHaveValue("");
  const selected = await selector.inputValue();
  await move(page, "e2", "e4");
  await expect
    .poll(
      async () =>
        new Chess(
          (await page.locator(".board-frame").getAttribute("data-fen"))!,
        ).get("e4")?.type,
    )
    .toBe("p");
  const fen = await page.locator(".board-frame").getAttribute("data-fen");
  await page.keyboard.press("f");
  await expect(selector).toHaveValue(selected);
  await nav(page, "Settings");
  await nav(page, "Builder");
  await expect(selector).toHaveValue(selected);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", fen!);
  await page.reload();
  await nav(page, "Builder");
  await expect(selector).toHaveValue(selected);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", fen!);
  await page.setViewportSize({ width: 390, height: 844 });
  await boardVisible(page);
});

test("Black Train prompt remains playable with a fully visible narrow board", async ({
  page,
}) => {
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "black.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(pgn),
  });
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Black", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Import repertoire", exact: true })
    .click();
  await page.getByRole("button", { name: "View imported repertoire" }).click();
  await nav(page, "Train");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(async () =>
      new Chess(
        (await page.locator(".board-frame").getAttribute("data-fen"))!,
      ).turn(),
    )
    .toBe("b");
  await expect(page.locator(".board-frame")).toHaveAttribute(
    "data-input-enabled",
    "true",
  );
  await boardVisible(page);
  const box = await page.locator(".board-frame").boundingBox();
  expect(box!.width).toBeGreaterThan(200);
  await move(page, "e7", "e5");
  await expect
    .poll(
      async () =>
        new Chess(
          (await page.locator(".board-frame").getAttribute("data-fen"))!,
        ).get("e5")?.type,
    )
    .toBe("p");
});

test("sample deletion uses repertoire identity and does not delete its same-filename sibling", async ({
  page,
  request,
}) => {
  for (const color of ["white", "black"])
    await request.post(`${api}/imports/pgn`, {
      multipart: {
        file: {
          name: "Tempo examples.pgn",
          mimeType: "application/x-chess-pgn",
          buffer: Buffer.from(pgn),
        },
        trained_color: color,
        initial_depth: "2",
      },
    });
  await page.goto("/");
  await nav(page, "Repertoire");
  await expect(page.locator(".repertoire-card")).toHaveCount(2);
  page.on("dialog", (dialog) => dialog.accept());
  await page
    .locator(".repertoire-card")
    .first()
    .getByRole("button", { name: "Delete", exact: true })
    .click();
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
  await nav(page, "Builder");
  await nav(page, "Repertoire");
  await page.reload();
  await nav(page, "Repertoire");
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
});

test("Builder exact transposition saves the played route and does not prompt for a covered route", async ({
  page,
  request,
}) => {
  await request.post(`${api}/imports/pgn`, {
    multipart: {
      file: {
        name: "transposition.pgn",
        mimeType: "application/x-chess-pgn",
        buffer: Buffer.from('[Event "Blitz"]\n\n1. d4 d5 2. Nf3 Nf6 *'),
      },
      initial_depth: "2",
    },
  });
  await page.goto("/");
  await nav(page, "Builder");
  await move(page, "g1", "f3");
  await move(page, "d7", "d5");
  await move(page, "d2", "d4");
  await expect(
    page.getByRole("button", { name: "Add as branch" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Add as branch" }).click();
  await page.getByRole("button", { name: "Save branch" }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`${api}/repertoire/lines`)).json()).lines
          .length,
    )
    .toBe(2);
  await expect(page.getByRole("button", { name: "Add as branch" })).toHaveCount(
    0,
  );
  await page.reload();
  await nav(page, "Builder");
  await expect(page.getByRole("button", { name: "Add as branch" })).toHaveCount(
    0,
  );
  await boardVisible(page);
});

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
  const catalog = await request.get("/data/tactics-decks.json");
  expect(catalog.ok()).toBeTruthy();
  const deck = (await catalog.json()).filter(
    (record: { DeckId: string }) => record.DeckId === "hangingPiece-easy",
  );
  expect(deck).toHaveLength(100);
  for (const path of ["Move", "Capture"])
    expect(
      (await request.get(`/sounds/standard/${path}.mp3`)).ok(),
    ).toBeTruthy();
  let requests = 0;
  const progress = await (await request.get(`${api}/tactics/progress`)).json();
  const discovered = new Set(
    progress["hangingPiece:easy"]?.discoveredIds ??
      progress["hangingPiece:easy"]?.cleanIds ??
      [],
  );
  const expectedPuzzle = deck.find(
    (record: { PuzzleId: string }) =>
      !discovered.has(`lichess-${record.PuzzleId}`),
  ).DeckPosition;
  page.on("request", (request) => {
    if (request.url().includes("/data/tactics-decks.json")) requests++;
  });
  await page.goto("/");
  await expect.poll(() => requests).toBe(1);
  await nav(page, "Tactics");
  await expect(page.locator(".board-frame")).toBeVisible();
  await expect(page.getByText(`Puzzle ${expectedPuzzle} of 100`)).toBeVisible();
  expect(requests).toBe(1);
  await boardVisible(page);
});

test("Builder source comparison is immediately reachable beside the board", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem("tempo-stockfish-on", "true"),
  );
  await page.goto("/");
  await nav(page, "Builder");
  const comparison = page.getByRole("table", {
    name: "Move source comparison",
  });
  await expect(comparison).toBeVisible();
  for (const name of ["Stockfish", "Maia", "Lichess", "Masters"])
    await expect(
      comparison.getByRole("columnheader", { name, exact: true }),
    ).toBeVisible();
  await expect(comparison.locator("tbody tr button").first()).toBeVisible({
    timeout: 45_000,
  });
  const header = comparison.getByRole("columnheader", {
    name: "Stockfish",
    exact: true,
  });
  await header.getByRole("button").focus();
  await page.keyboard.press("Enter");
  await expect(header).toHaveAttribute("aria-sort", "ascending");
  await page.keyboard.press("Enter");
  await expect(header).toHaveAttribute("aria-sort", "descending");
  await expect(
    page
      .getByRole("navigation", { name: "Primary navigation" })
      .getByRole("button", { name: "Builder", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.locator("h1:not(.sr-only)")).toHaveCount(0);
  await boardVisible(page);
});

test("Edit card opens Builder line-removal context and deletes the selected branch", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "branches.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(
      '[Event "QGD"]\n\n1. d4 d5 2. c4 e6 *\n\n[Event "Nimzo"]\n\n1. d4 Nf6 2. c4 e6 3. Nc3 Bb4 *',
    ),
  });
  await page
    .getByRole("button", { name: "Import repertoire", exact: true })
    .click();
  await page.getByRole("button", { name: "View imported repertoire" }).click();
  await nav(page, "Train");
  await expect(
    page.getByRole("button", { name: /Edit card/i }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Edit card/i })
    .first()
    .click();
  await page
    .getByRole("button", { name: "Open Builder to remove line" })
    .click();
  await expect(
    page
      .getByRole("navigation", { name: "Primary navigation" })
      .getByRole("button", { name: "Builder", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await move(page, "d2", "d4");
  await move(page, "g8", "f6");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete line from here" }).click();
  await expect(page.getByText(/Deleted \d+ line/)).toBeVisible();
  await expect
    .poll(async () => {
      const lines = (
        await (await request.get(`${api}/repertoire/lines`)).json()
      ).lines;
      return lines.some(
        (line: { moves: string[] }) =>
          line.moves?.[0] === "d2d4" && line.moves?.[1] === "g8f6",
      );
    })
    .toBe(false);
});

test("Builder right-click annotation saves the exact clicked square", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "annotation.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from('[Event "QGD"]\n\n1. d4 d5 2. c4 e6 *'),
  });
  await page
    .getByRole("button", { name: "Import repertoire", exact: true })
    .click();
  await page.getByRole("button", { name: "View imported repertoire" }).click();
  await nav(page, "Builder");
  await expect(
    page.getByRole("combobox", { name: "Active repertoire" }),
  ).not.toHaveValue("");

  const fen = await page
    .locator(".board-frame")
    .first()
    .getAttribute("data-fen");
  const repertoireId = (await (await request.get(`${api}/repertoires`)).json())
    .repertoires[0].id;

  await page.getByRole("textbox", { name: "Position comment" }).fill("probe");
  await page.getByRole("button", { name: "Save note" }).click();
  await expect
    .poll(async () => {
      const body = await (
        await request.get(
          `${api}/repertoires/${encodeURIComponent(repertoireId)}/annotations?fen=${encodeURIComponent(fen!)}`,
        )
      ).json();
      return body.annotations?.[0]?.comment ?? "";
    })
    .toBe("probe");

  await clickSquare(page, "a4", "right");
  await expect(page.getByText("Unsaved changes")).toBeVisible();
  await page.getByRole("button", { name: "Save note" }).click();

  await expect
    .poll(async () => {
      const body = await (
        await request.get(
          `${api}/repertoires/${encodeURIComponent(repertoireId)}/annotations?fen=${encodeURIComponent(fen!)}`,
        )
      ).json();
      const note = body.annotations?.[0];
      return (
        note?.squares?.map((item: { square: string }) => item.square) ?? []
      );
    })
    .toContain("a4");
  await expect
    .poll(async () => {
      const body = await (
        await request.get(
          `${api}/repertoires/${encodeURIComponent(repertoireId)}/annotations?fen=${encodeURIComponent(fen!)}`,
        )
      ).json();
      const note = body.annotations?.[0];
      return (
        note?.squares?.map((item: { square: string }) => item.square) ?? []
      );
    })
    .not.toContain("a3");
});
