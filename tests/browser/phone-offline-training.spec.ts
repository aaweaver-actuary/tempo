import { test, expect, type Page } from "@playwright/test";

async function playBoardSquare(page: Page, square: string) {
  const board = page.locator(".cg-wrap");
  const bounds = (await board.boundingBox())!;
  const file = square.charCodeAt(0) - 97;
  const rank = Number(square[1]) - 1;
  await page.mouse.click(
    bounds.x + ((file + 0.5) * bounds.width) / 8,
    bounds.y + ((7 - rank + 0.5) * bounds.height) / 8,
  );
}

test("local Tempo shell opens after the network drops", async ({ page }) => {
  await page.goto("/");
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  await expect(page.getByText(/Tempo update ready/)).toHaveCount(0);
  await expect.poll(() => page.evaluate(async () => Boolean(
    await (await caches.open("tempo-static-v5")).match("/pieces/merida/wP.svg"),
  ))).toBe(true);
  await page.context().setOffline(true);
  await page.reload();
  await expect(page.getByRole("button", { name: "Tempo home" })).toBeVisible();
});

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const today = new Date();
const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
const preparedCards = [
  { id: "phone-first", queue_entry_id: 501, latest_review_id: 0,
    start_fen: startFen, moves: ["e2e4"], content_type: "opening",
    repertoire_name: "First phone card", repertoire_source: "PGN", scheduling_mode: "normal" },
  { id: "phone-second", queue_entry_id: 502, latest_review_id: 0,
    start_fen: startFen, moves: ["d2d4"], content_type: "opening",
    repertoire_name: "Second phone card", repertoire_source: "PGN", scheduling_mode: "normal" },
];

test("prepared phone queue survives API outage reload and syncs its review", async ({ page }) => {
  const windowPayload = { local_date: localDate, count: 2, cards: preparedCards };
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: windowPayload }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ json: {
    ...windowPayload, prepared_at: new Date().toISOString(),
    projection: { state: "ready", generation: 1, updated_at: null,
      refresh_pending: 0, last_error: null, blocked_count: 0 },
  } }));
  await page.goto("/");
  await expect(page.getByText(`Phone queue prepared for ${localDate}.`)).toBeVisible();
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  await expect.poll(() => page.evaluate(async () => {
    const cache = await caches.open("tempo-static-v5");
    return (await cache.keys()).map((request) => new URL(request.url).pathname)
      .filter((path) => path.startsWith("/assets/")).length;
  })).toBeGreaterThanOrEqual(2);
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByText("First phone card")).toBeVisible();
  await expect(page.getByText(/Prepared phone queue for/)).toBeVisible();
  await page.getByRole("button", { name: "Correct" }).click();
  await expect(page.getByText("Second phone card")).toBeVisible();
  await expect(page.getByText(/Saved on phone/)).toBeVisible();
  await page.getByRole("button", { name: "Correct" }).click();
  await expect(page.getByText("First phone card")).toBeVisible();
  await page.getByRole("button", { name: "Correct" }).click();
  await expect(page.getByText("Second phone card")).toBeVisible();
  await page.reload();
  await expect(page.getByText("Second phone card")).toBeVisible();

  const replayedEntries: number[] = [];
  await page.route("**/api/cards/phone-*/review", async (route) => {
    const body = route.request().postDataJSON() as { queue_entry_id: number };
    replayedEntries.push(body.queue_entry_id);
    await route.fulfill({ json: {
      persisted: true, review_id: 601 + replayedEntries.length,
      requeue_entry_id: body.queue_entry_id === 501 ? 602
        : body.queue_entry_id === 502 ? 603 : null,
    } });
  });
  await page.unroute("**/api/**");
  await page.reload();
  await expect.poll(() => replayedEntries.length).toBe(3);
  expect(replayedEntries).toEqual([501, 502, 602]);
  await page.reload();
  expect(replayedEntries).toEqual([501, 502, 602]);
});

test("yesterday's prepared phone queue never becomes today's training", async ({ page }) => {
  const windowPayload = { local_date: localDate, count: 2, cards: preparedCards };
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: windowPayload }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ json: {
    ...windowPayload, prepared_at: new Date().toISOString(),
    projection: { state: "ready", generation: 1, updated_at: null,
      refresh_pending: 0, last_error: null, blocked_count: 0 },
  } }));
  await page.goto("/");
  await expect(page.getByText(`Phone queue prepared for ${localDate}.`)).toBeVisible();
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.clock.setFixedTime(new Date(Date.now() + 24 * 60 * 60 * 1000));
  await page.reload();
  await expect(page.getByText("First phone card")).not.toBeVisible();
  await expect(page.getByText(/The local queue could not be loaded/)).toBeVisible();
});

test("offline guided failure stays guided after reopening the phone app", async ({ page }) => {
  const windowPayload = { local_date: localDate, count: 2, cards: preparedCards };
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: windowPayload }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ json: {
    ...windowPayload, prepared_at: new Date().toISOString(),
    projection: { state: "ready", generation: 1, updated_at: null,
      refresh_pending: 0, last_error: null, blocked_count: 0 },
  } }));
  await page.goto("/");
  await expect(page.getByText(`Phone queue prepared for ${localDate}.`)).toBeVisible();
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByText("First phone card")).toBeVisible();
  await playBoardSquare(page, "g2");
  await playBoardSquare(page, "g4");
  await expect(page.getByText("Guided attempt saved on phone.")).toBeVisible();
  await page.reload();
  await expect(page.getByText("First phone card")).toBeVisible();
  await expect(page.getByRole("button", { name: "Finish on the board" })).toBeVisible();
});

test("older computer API blocks phone preparation with an update instruction", async ({ page }) => {
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    local_date: localDate, count: 2, cards: preparedCards,
  } }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ status: 404, json: { detail: "Not Found" } }));
  await page.goto("/");
  await expect(page.getByText(/Tempo on the computer is an older version/)).toBeVisible();
  await expect(page.getByText(/Phone queue prepared for/)).toHaveCount(0);
});

test("service worker update during an active phone attempt waits for a safe reopen", async ({ page }) => {
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    local_date: localDate, count: 2, cards: preparedCards,
  } }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ json: {
    local_date: localDate, count: 2, cards: preparedCards,
    prepared_at: new Date().toISOString(),
    projection: { state: "ready", generation: 1, updated_at: null,
      refresh_pending: 0, last_error: null, blocked_count: 0 },
  } }));
  await page.goto("/");
  await expect(page.getByText("First phone card")).toBeVisible();
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  await page.evaluate(() => window.dispatchEvent(new Event("tempo:update-ready")));
  await expect(page.getByText(/Tempo update ready. Finish this attempt/)).toBeVisible();
  await expect(page.getByText("First phone card")).toBeVisible();
});
