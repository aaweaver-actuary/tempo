import { test, expect, api, nav, move } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";
import { gameSyncFixture } from "./product-game-sync-fixture";

test("both synthetic providers retain the current attempt and settle actual game feedback through normal Study", async ({ page, request }) => {
  test.setTimeout(180_000);
  const fixture = await gameSyncFixture(request, api, "seed");
  await prepareUI(page);
  const board = page.locator(".persistent-board-shell .board-frame").first();
  await expect(board).toHaveAttribute("data-fen", fixture.unrelated_fen);
  let original: Record<string, unknown>;
  await expect.poll(async () => {
    original = await gameSyncFixture(request, api, "inspect", fixture);
    return (original.active as { issued_as_head: number }).issued_as_head;
  }).toBe(1);

  await page.route("**/api/product/game-review/games", route => route.fulfill({ status: 503, body: "unavailable" }));
  await nav(page, "Game Review");
  await expect(page.getByRole("alert").filter({ hasText: "Game Review request failed" })).toBeVisible();
  expect((await gameSyncFixture(request, api, "inspect", fixture)).active).toEqual(original!.active);
  await page.unroute("**/api/product/game-review/games");

  await nav(page, "Settings");
  const settings = page.getByRole("region", { name: "Game sync settings", exact: true });
  for (const provider of ["lichess", "chesscom"]) {
    await settings.getByLabel("Game provider").selectOption(provider);
    await expect(settings.getByLabel("Game account")).toBeEnabled();
    await settings.getByLabel("Game account").fill("Learner");
    await settings.getByLabel("Games from (UTC)").fill("2026-09-01T00:00");
    await settings.getByLabel("Games until (UTC)").fill("2026-11-01T00:00");
    await settings.getByRole("checkbox", { name: "rapid", exact: true }).check();
    await settings.getByRole("checkbox", { name: "Game sync fixture repertoire", exact: true }).check();
    await settings.getByLabel("Rated games").selectOption("rated");
    await settings.getByRole("button", { name: "Save game sync settings", exact: true }).click();
    await expect(settings.getByText("Game sync settings saved.", { exact: true })).toBeVisible();
    await settings.getByRole("button", { name: "Run saved game sync", exact: true }).click();
    await expect(settings.getByRole("status")).toContainText("pending");
  }
  expect(await gameSyncFixture(request, api, "run", fixture)).toMatchObject({ complete: true, sources: 207, effects: 2 });
  expect((await gameSyncFixture(request, api, "inspect", fixture)).active).toEqual(original!.active);

  await nav(page, "Game Review");
  const review = page.getByRole("region", { name: "Game Review", exact: true });
  await review.getByRole("button").filter({ hasText: `Lichess · ${fixture.game_id}` }).click();
  await expect(review.getByText("Study feedback: applied", { exact: true })).toBeVisible();
  await expect(review.locator(".game-review-finding")).toContainText(fixture.card_id);
  await review.getByRole("button", { name: "Show position before ply 1", exact: true }).click();
  await expect(review.locator(".board-frame")).toHaveAttribute("data-fen", fixture.start_fen);
  await expect(review.getByText("Ply 0 of 1", { exact: true })).toBeVisible();

  await nav(page, "Train");
  await expect(board).toHaveAttribute("data-fen", fixture.unrelated_fen);
  const unrelatedSaved = page.waitForResponse(response => response.url().includes(`/cards/${fixture.unrelated_card}/review`) && response.request().method() === "POST");
  await move(page, "e2", "e4");
  expect((await unrelatedSaved).ok()).toBe(true);
  await page.keyboard.press("Enter");
  await expect(board).toHaveAttribute("data-fen", fixture.start_fen);
  const saved = page.waitForResponse(response => response.url().includes(`/cards/${fixture.card_id}/review`) && response.request().method() === "POST");
  await move(page, "e2", "e4");
  expect((await saved).ok()).toBe(true);
  await expect.poll(async () => (await gameSyncFixture(request, api, "inspect", fixture)).settled).toBe(2);
  await page.reload();
  await nav(page, "Game Review");
  await review.getByRole("button").filter({ hasText: `Lichess · ${fixture.game_id}` }).click();
  await expect(review.getByText("Study feedback: settled", { exact: true })).toBeVisible();
});
