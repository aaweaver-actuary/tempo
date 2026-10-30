import { test, expect, prepareControlledUI } from "./fixtures";
import fixture from "../fixtures/game-review-current-api.json" with { type: "json" };

test("Games refresh fetches now and updates the list without moving the selected game", async ({ page }) => {
  const api = await prepareControlledUI(page, undefined, false);
  let operation = "", ready = false, submissions = 0;
  const status = () => ({ provider: "lichess", operation_id: operation, job_id: `job:${operation}`,
    status: ready ? "complete" : "queued", pages_committed: ready ? 2 : 0, next_attempt_at: 0, error: null,
    window: { start_at: "2026-10-25T00:00:00Z", end_at: "2026-10-30T12:00:00Z", precision: "millisecond" } });
  api.register({ method: "GET", path: "/api/product/game-review/games", query: {} }, route => route.fulfill({ json: {
    ...fixture.recent, games: ready ? [...fixture.recent.games, { ...fixture.recent.games[0], game_id: "fresh-game" }] : fixture.recent.games,
  } }));
  api.register({ method: "GET", path: fixture.detail_path, query: {} }, route => route.fulfill({ json: fixture.detail }));
  api.register({ method: "POST", path: "/api/product/game-review/refresh", query: {}, validate: request => {
    const body = request.body as { provider: string; operation_id: string };
    expect(Object.keys(body).sort()).toEqual(["operation_id", "provider"]);
    expect(body.provider).toBe("lichess");
    expect(body.operation_id).toBeTruthy();
    operation = body.operation_id; submissions++;
  } }, route => route.fulfill({ json: status() }));
  api.register({ method: "GET", path: () => `/api/product/game-review/refresh/${encodeURIComponent(operation)}`, query: {} },
    route => route.fulfill({ json: status() }));
  await page.goto("/");
  await page.getByRole("button", { name: "Games", exact: true }).first().click();
  await page.getByRole("button", { name: new RegExp(`Lichess · ${fixture.detail.game_id}`) }).click();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  const position = await page.getByText(/^Ply \d+ of/).textContent();
  await page.getByRole("button", { name: "Refresh Lichess now" }).click();
  await expect(page.getByText("Lichess refresh: Queued", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith("tempo-pending-game-refresh-v1:")))).toBe(true);
  ready = true;
  await expect(page.getByRole("button", { name: /Lichess · fresh-game/ })).toBeVisible();
  await expect(page.getByText(/^Ply \d+ of/)).toHaveText(position!);
  await expect(page.getByText("Lichess refresh: Completed", { exact: true })).toBeVisible();
  expect(submissions).toBe(1);
});
