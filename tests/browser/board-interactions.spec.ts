import {
  test,
  expect,
  Chess,
  api,
  blackPgn,
  nav,
  boardVisible,
  move,
} from "./product-fixtures";
import type { APIRequestContext } from "@playwright/test";
import { prepareVisualUI } from "./visual-fixtures";

async function waitForPriorFixturePublication(request: APIRequestContext) {
  // Prior fixture deletion schedules queue publication. Let that empty
  // generation settle before the UI import's own publication begins.
  await expect.poll(async () => {
    const response = await request.get(`${api}/queue/today`, { headers: { "X-Tempo-Work-Class": "background" } });
    if (response.status() === 503 && response.headers()["retry-after"]) return null;
    expect(response.ok()).toBe(true);
    const queue = await response.json();
    return { state: queue.projection.state, pending: queue.projection.refresh_pending, count: queue.cards.length };
  }, { timeout: 30_000, message: "Previous fixture publication settles empty before Black UI import" })
    .toEqual({ state: "ready", pending: 0, count: 0 });
}

test("checked king gets a persistent translucent red cue that clears when the position changes", async ({ page }) => {
  const startingFen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4";
  const checkCard = {
    id: "checked-king-visual-card",
    queue_entry_id: 991,
    start_fen: startingFen,
    moves: ["h5f7"],
    content_type: "opening" as const,
    repertoire_name: "Scholar's mate check cue",
    repertoire_source: "PGN",
    first_correct_at: "2026-09-17T12:00:00Z",
    trained_color: "white" as const,
  };
  await prepareVisualUI(page, false, [checkCard]);
  await nav(page, "Train");
  const board = page.locator(".board-frame").first();
  const expectedPosition = new Chess(startingFen);
  expectedPosition.move({ from: "h5", to: "f7", promotion: "q" });
  await expect(board).toHaveAttribute("data-fen", startingFen);
  await move(page, "h5", "f7");
  await expect(board).toHaveAttribute("data-fen", expectedPosition.fen());

  const checkedSquare = board.locator(".cg-wrap cg-board square.check:visible");
  const checkedKing = board.locator(".cg-wrap piece.king.black");
  await expect(checkedSquare).toHaveCount(1);
  const [squareBounds, kingBounds] = await Promise.all([
    checkedSquare.boundingBox(),
    checkedKing.boundingBox(),
  ]);
  expect(squareBounds).not.toBeNull();
  expect(kingBounds).not.toBeNull();
  expect(
    Math.abs(
      squareBounds!.x + squareBounds!.width / 2 -
        (kingBounds!.x + kingBounds!.width / 2),
    ),
  ).toBeLessThan(1);
  expect(
    Math.abs(
      squareBounds!.y + squareBounds!.height / 2 -
        (kingBounds!.y + kingBounds!.height / 2),
    ),
  ).toBeLessThan(1);
  expect(await checkedSquare.evaluate((square) => getComputedStyle(square).backgroundImage)).toContain("radial-gradient");

  await page.keyboard.press("f");
  await expect(board).toHaveAttribute("data-orientation", "black");
  await expect(checkedSquare).toHaveCount(1);
  const [flippedSquareBounds, flippedKingBounds] = await Promise.all([
    checkedSquare.boundingBox(),
    checkedKing.boundingBox(),
  ]);
  expect(flippedSquareBounds).not.toBeNull();
  expect(flippedKingBounds).not.toBeNull();
  expect(
    Math.abs(
      flippedSquareBounds!.x + flippedSquareBounds!.width / 2 -
        (flippedKingBounds!.x + flippedKingBounds!.width / 2),
    ),
  ).toBeLessThan(1);
  expect(
    Math.abs(
      flippedSquareBounds!.y + flippedSquareBounds!.height / 2 -
        (flippedKingBounds!.y + flippedKingBounds!.height / 2),
    ),
  ).toBeLessThan(1);

  await page.keyboard.press("ArrowLeft");
  expectedPosition.undo();
  await expect(board).toHaveAttribute("data-fen", expectedPosition.fen());
  await expect(checkedSquare).toHaveCount(0);
});


test("local import respects the daily limit and keeps the Black Study queue playable", async ({
  page,
  request,
}) => {
  await waitForPriorFixturePublication(request);
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "mine.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(blackPgn),
  });
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Black", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Import repertoire", exact: true })
    .click();
  await expect(page.getByText(/2 cards are in today’s queue/)).toBeVisible({ timeout: 30_000 });
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

});

test("Black Train prompt remains playable with a fully visible narrow board", async ({
  page,
  request,
}) => {
  await waitForPriorFixturePublication(request);
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "black.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(blackPgn),
  });
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Black", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Import repertoire", exact: true })
    .click();
  await expect(page.getByText(/2 cards are in today’s queue/)).toBeVisible({ timeout: 30_000 });
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
  const prompt = new Chess(
    (await page.locator(".board-frame").getAttribute("data-fen"))!,
  );
  const response = prompt.get("d4")
    ? { from: "d7" as const, to: "d5" as const }
    : { from: "e7" as const, to: "e5" as const };
  await move(page, response.from, response.to);
  await expect
    .poll(
      async () =>
        new Chess(
          (await page.locator(".board-frame").getAttribute("data-fen"))!,
        ).get(response.to)?.type,
    )
    .toBe("p");
});
