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
