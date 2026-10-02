import { test, expect, Chess, api, nav, move } from "./product-fixtures";

const startingFen = new Chess().fen();
const screenshotMoves = ["d2d4", "g8f6", "c1f4", "d7d5", "e2e3", "b8c6", "c2c4", "d5c4", "b1c3"];
const bishopMoves = ["d2d4", "d7d5", "c1f4", "e7e6", "e2e3", "b8c6", "c2c4", "d5c4", "f1c4"];
const transposedMoves = ["d2d4", "e7e6", "c1f4", "d7d5", "e2e3", "b8c6", "c2c4", "d5c4", "f1c4"];
const cards = [
  ["screenshot", screenshotMoves], ["bishop", bishopMoves], ["transposed", transposedMoves],
].map(([id, moves]) => ({
  id, start_fen: startingFen, moves, kind: "prefix", state: "learning",
  trained_color: "white", repertoires: [{ id: "london", name: "London System" }],
}));

test("four comparison boards retain distinct routes and independent ply navigation", async ({ page }) => {
  await page.route("**/api/compare/cards", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({ cards }),
  }));
  await page.goto("/");
  await nav(page, "Builder");
  for (const uci of screenshotMoves.slice(0, 8)) await move(page, uci.slice(0, 2), uci.slice(2, 4));
  const sourcePosition = new Chess();
  for (const uci of screenshotMoves.slice(0, 8)) sourcePosition.move(uci);
  const sourceFen = sourcePosition.fen();
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", sourceFen);
  await page.getByRole("button", { name: "Compare positions" }).click();
  await expect(page.getByRole("heading", { name: "Compare positions" })).toBeVisible();
  await expect(page.locator(".comparison-tile")).toHaveCount(1);
  await expect(page.getByText("2 piece relocations")).toBeVisible();
  await page.getByRole("button", { name: /London System · ply 8 · Nc3/ }).click();
  await page.getByRole("button", { name: /London System · ply 8 · Bxc4/ }).first().click();
  await page.getByRole("button", { name: /London System · ply 8 · Bxc4/ }).last().click();
  await expect(page.locator(".comparison-tile")).toHaveCount(4);
  await page.locator(".comparison-tile").nth(2).getByRole("button", { name: "Search from here" }).click();
  await expect(page.getByText("Exact transposition")).toBeVisible();
  await page.locator(".comparison-tile").nth(1).getByRole("button", { name: "Start" }).click();
  await expect(page.locator(".comparison-tile").first().locator(".board-frame"))
    .toHaveAttribute("data-fen", sourceFen!);
  await expect(page.locator(".comparison-tile").nth(1).locator(".board-frame"))
    .toHaveAttribute("data-fen", startingFen);
  const activeBoard = page.locator(".comparison-tile").nth(1).locator(".board-frame");
  await page.keyboard.press("f");
  await expect(activeBoard).toHaveAttribute("data-orientation", "black");
  await expect(page.locator(".comparison-tile").first().locator(".board-frame")).toHaveAttribute("data-orientation", "white");
  await page.keyboard.press("ArrowRight");
  const firstMove = new Chess(); firstMove.move("d4");
  await expect(activeBoard).toHaveAttribute("data-fen", firstMove.fen());
  await page.keyboard.press("r");
  const pinnedPosition = new Chess(); for (const uci of screenshotMoves.slice(0, 8)) pinnedPosition.move(uci);
  await expect(activeBoard).toHaveAttribute("data-fen", pinnedPosition.fen());
  await expect(activeBoard).toHaveAttribute("data-orientation", "white");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth))
    .toBeLessThanOrEqual(1);
  await expect(page.locator(".comparison-tile")).toHaveCount(4);
  await page.getByRole("button", { name: "Return to Builder" }).click();
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", sourceFen!);
  await page.getByRole("button", { name: "Compare positions" }).click();
  await expect(page.locator(".comparison-tile")).toHaveCount(4);
});

test("training comparison stays concealed until a mistake and returning preserves the attempt", async ({ page, request }) => {
  const settings = await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`, { data: { ...settings, new_cards_per_day: 10 } });
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: "comparison-training.pgn", mimeType: "application/x-chess-pgn",
      buffer: Buffer.from('[Event "London"]\n\n1. d4 Nf6 2. Bf4 d5 3. e3 Nc6 4. c4 dxc4 5. Nc3 *') },
    initial_depth: "2",
  } });
  expect(imported.ok()).toBeTruthy();
  await expect.poll(async () => (await (await request.get(`${api}/queue/today`)).json()).cards.length,
    { timeout: 20_000 }).toBeGreaterThan(0);
  await page.goto("/");
  await nav(page, "Train");
  await expect(page.getByRole("button", { name: "Compare positions" })).toHaveCount(0);
  const initialFen = await page.locator(".board-frame").first().getAttribute("data-fen");
  await page.getByRole("button", { name: "Again", exact: true }).click();
  await expect(page.getByRole("button", { name: "Compare positions" })).toBeVisible();
  await page.getByRole("button", { name: "Compare positions" }).click();
  await expect(page.getByRole("heading", { name: "Compare positions" })).toBeVisible();
  await page.getByRole("button", { name: "Return to Training" }).click();
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", initialFen!);
  await expect(page.getByRole("button", { name: "Compare positions" })).toBeVisible();
});

test("comparison card service failure gives a retry without sample matches", async ({ page }) => {
  await page.route("**/api/compare/cards", (route) => route.fulfill({ status: 503 }));
  await page.goto("/");
  await nav(page, "Builder");
  await page.getByRole("button", { name: "Compare positions" }).click();
  await expect(page.getByRole("alert")).toContainText("Cards could not be loaded");
  await expect(page.getByRole("alert")).toContainText("HTTP 503");
  await expect(page.getByRole("button", { name: /London System · ply/ })).toHaveCount(0);
  await page.unroute("**/api/compare/cards");
  await page.route("**/api/compare/cards", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({ cards }),
  }));
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("button", { name: /London System · ply/ }).first()).toBeVisible();
});
