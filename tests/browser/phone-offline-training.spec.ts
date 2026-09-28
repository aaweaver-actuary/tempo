import { test, expect, type Page } from "@playwright/test";

test.use({ userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1" });
test.beforeEach(async ({ context }) => {
  await context.addInitScript(() => Object.defineProperty(navigator, "standalone", { value: true, configurable: true }));
});

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
    await (await caches.open("tempo-static-v6")).match("/pieces/merida/wP.svg"),
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
  expect(await page.evaluate(() => ({ userAgent: navigator.userAgent, standalone: (navigator as Navigator & { standalone?: boolean }).standalone }))).toMatchObject({ standalone: true });
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  await expect.poll(() => page.evaluate(async () => {
    const cache = await caches.open("tempo-static-v6");
    return (await cache.keys()).map((request) => new URL(request.url).pathname)
      .filter((path) => path.startsWith("/assets/")).length;
  })).toBeGreaterThanOrEqual(2);
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByText("First phone card")).toBeVisible();
  await expect(page.getByRole("main").getByText(/Offline queue prepared/)).toBeVisible();
  await expect(page.getByRole("main").getByText(/Live service:.*Retry sync when connected/)).toBeVisible();
  await page.getByRole("button", { name: "Correct" }).click();
  await expect(page.getByText("Second phone card")).toBeVisible();
  await expect(page.getByRole("main").getByText(/saved on phone/i)).toBeVisible();
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

test("phone 225-card offline queue reconciles to the desktop 241-card count and next card after reconnect", async ({ page, browser }) => {
  const queueCards = (count: number, prefix: string) => Array.from({ length: count }, (_, index) => ({
    ...preparedCards[0], id: `${prefix}-${index}`, queue_entry_id: 10_000 + index,
    repertoire_name: index === 0 ? `${prefix} first card` : `${prefix} card ${index}`,
  }));
  const phoneCards = queueCards(225, "old");
  const desktopCards = queueCards(241, "new");
  let phoneConnected = true;
  let authoritativeCards = phoneCards;
  const queuePayload = (cards: typeof phoneCards) => ({
    local_date: localDate, count: cards.length, cards,
    projection: { state: "ready", generation: 1, updated_at: null,
      refresh_pending: 0, last_error: null, blocked_count: 0 },
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/queue/window?**", (route) => phoneConnected
    ? route.fulfill({ json: { ...queuePayload(authoritativeCards), cards: authoritativeCards.slice(0, 20) } })
    : route.abort("internetdisconnected"));
  await page.route("**/api/queue/prepared", (route) => phoneConnected
    ? route.fulfill({ json: { ...queuePayload(authoritativeCards), prepared_at: new Date().toISOString() } })
    : route.abort("internetdisconnected"));
  await page.goto("/");
  await expect(page.getByText(`Phone queue prepared for ${localDate}.`)).toBeVisible();
  phoneConnected = false;
  await page.reload();
  await expect(page.getByText("Offline queue", { exact: true })).toBeVisible();
  await expect(page.getByRole("main").getByText(/live count may differ until you reconnect/)).toBeVisible();
  await expect(page.locator(".session-count strong")).toHaveText("225");
  await expect(page.getByText("old first card")).toBeVisible();

  authoritativeCards = desktopCards;
  const desktopContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  try {
    const desktop = await desktopContext.newPage();
    await desktop.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
      ...queuePayload(authoritativeCards), cards: authoritativeCards.slice(0, 20),
    } }));
    await desktop.route("**/api/queue/prepared", (route) => route.fulfill({ json: {
      ...queuePayload(authoritativeCards), prepared_at: new Date().toISOString(),
    } }));
    await desktop.goto("/");
    await expect(desktop.locator(".session-count strong")).toHaveText("241");
    await expect(desktop.getByText("new first card")).toBeVisible();

    phoneConnected = true;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect(page.locator(".session-count strong")).toHaveText("241");
    await expect(page.getByText("new first card")).toBeVisible();
    await expect(page.getByText("Offline queue", { exact: true })).toHaveCount(0);
  } finally {
    await desktopContext.close();
  }
});

test("pending phone review remains saved while live training opens and a conflict remains recorded after reconciliation", async ({ page }) => {
  const replacement = { ...preparedCards[0], id: "replacement", queue_entry_id: 601,
    repertoire_name: "Replacement card" };
  let canonicalCards = preparedCards;
  const payload = () => ({ local_date: localDate, count: canonicalCards.length, cards: canonicalCards,
    projection: { state: "ready", generation: 1, updated_at: null,
      refresh_pending: 0, last_error: null, blocked_count: 0 } });
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: payload() }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ json: {
    ...payload(), prepared_at: new Date().toISOString(),
  } }));
  await page.goto("/");
  await expect(page.getByText(`Phone queue prepared for ${localDate}.`)).toBeVisible();
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByText("Offline queue", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Correct" }).click();
  await expect(page.getByRole("main").getByText(/1 review saved on phone/)).toBeVisible();

  canonicalCards = [replacement, preparedCards[1], {
    ...preparedCards[1], id: "added", queue_entry_id: 602, repertoire_name: "Added card",
  }];
  await page.route("**/api/cards/phone-first/review", (route) => route.fulfill({ status: 503,
    json: { detail: "Review service unavailable" } }));
  await page.unroute("**/api/**");
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByText("Offline queue", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("main").getByText(/Could not save a previous training review/)).toBeVisible();
  await expect(page.locator(".session-count strong")).toHaveText("3");

  await page.unroute("**/api/cards/phone-first/review");
  await page.route("**/api/cards/phone-first/review", (route) => route.fulfill({ status: 409,
    json: { detail: "This card was reviewed on another device" } }));
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.locator(".session-count strong")).toHaveText("3");
  await expect(page.getByText("Second phone card")).toBeVisible();
  await expect(page.getByText(/1 phone review\(s\) remain saved/)).toBeVisible();
  const savedQueue = await page.evaluate(() => new Promise<{
    conflict?: string; cardIds: string[];
  }>((resolve, reject) => {
    const opened = indexedDB.open("tempo-offline-training", 1);
    opened.onerror = () => reject(opened.error);
    opened.onsuccess = () => {
      const request = opened.result.transaction("training").objectStore("training").get("prepared-daily-queue");
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve({
        conflict: request.result?.attempts?.[0]?.conflict,
        cardIds: request.result?.cards?.map((card: { id: string }) => card.id),
      });
    };
  }));
  expect(savedQueue.conflict).toContain("another device");
  expect(savedQueue.cardIds).toContain("replacement");
});

test("competing computer review credits the saved phone result and explains the schedule fallback", async ({ page }) => {
  const payload = { local_date: localDate, count: preparedCards.length, cards: preparedCards,
    projection: { state: "ready", generation: 1, updated_at: null,
      refresh_pending: 0, last_error: null, blocked_count: 0 } };
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: payload }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ json: {
    ...payload, prepared_at: new Date().toISOString(),
  } }));
  await page.goto("/");
  await expect(page.getByText(`Phone queue prepared for ${localDate}.`)).toBeVisible();
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByText("Offline queue", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Correct" }).click();
  await expect(page.getByRole("main").getByText(/1 review saved on phone/)).toBeVisible();
  await page.route("**/api/cards/phone-first/review", (route) => route.fulfill({ json: {
    persisted: true, review_id: 712, requeue_entry_id: null,
    reconciliation: "computer_fallback",
    warning: "Both results are credited; the computer schedule was kept with a near-term review.",
    competing_review: { outcome: "again", completed_at: "2026-09-28T15:00:00Z" },
  } }));
  await page.unroute("**/api/**");
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.locator(".notification-toast").getByText(/computer schedule was kept/)).toBeVisible();
  const savedAttempt = await page.evaluate(() => new Promise<{ serverReviewId?: number; syncWarning?: string }>((resolve, reject) => {
    const opened = indexedDB.open("tempo-offline-training", 1);
    opened.onerror = () => reject(opened.error);
    opened.onsuccess = () => {
      const request = opened.result.transaction("training").objectStore("training").get("prepared-daily-queue");
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result.attempts[0]);
    };
  }));
  expect(savedAttempt.serverReviewId).toBe(712);
  expect(savedAttempt.syncWarning).toContain("computer schedule");
});

test("nine phone conflicts stay in the notification tray without moving training", async ({ page }) => {
  const payload = { local_date: localDate, count: preparedCards.length, cards: preparedCards,
    projection: { state: "ready", generation: 1, updated_at: null, refresh_pending: 0, last_error: null, blocked_count: 0 } };
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: payload }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ json: { ...payload, prepared_at: new Date().toISOString() } }));
  await page.goto("/");
  await expect(page.getByText(`Phone queue prepared for ${localDate}.`)).toBeVisible();
  const board = page.locator(".unified-board-shell-panel");
  const before = (await board.boundingBox())!;
  const cardIds = Array.from({ length: 9 }, (_, index) => `${index}`.repeat(64));
  await page.evaluate(async (ids) => {
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open("tempo-offline-training", 1);
      request.onupgradeneeded = () => reject(new Error("Prepared phone queue was not saved before the conflict fixture"));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    await new Promise<void>((resolve, reject) => {
      const transaction = database.transaction("training", "readwrite");
      const store = transaction.objectStore("training");
      const request = store.get("prepared-daily-queue");
      request.onsuccess = () => {
        try {
          const saved = request.result;
          if (!saved) throw new Error("Prepared phone queue fixture is missing");
          saved.attempts = ids.map((cardId: string, index: number) => ({
            localEntryId: 900 + index, cardId, outcome: "correct", guided: false,
            completedAt: new Date().toISOString(), expectedReviewId: 0, expectedRevision: 1,
            conflict: "This card was reviewed on another device",
          }));
          store.put(saved, "prepared-daily-queue");
        } catch (failure) { reject(failure); }
      };
      request.onerror = () => reject(request.error);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
    database.close();
  }, cardIds);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.locator(".notification-toast").getByText(/9 phone review\(s\) remain saved/)).toBeVisible();
  const after = (await board.boundingBox())!;
  expect(Math.abs(after.y - before.y)).toBeLessThanOrEqual(1);
  expect(Math.abs(after.x - before.x)).toBeLessThanOrEqual(1);
  await expect(page.locator(".notification-toast")).toHaveCount(0, { timeout: 12_000 });
  await page.reload();
  await page.getByRole("button", { name: "Notifications" }).click();
  await page.getByRole("button", { name: "warning", exact: true }).click();
  await expect(page.locator(".notification-list").getByText(/9 phone review\(s\) remain saved/)).toBeVisible();
  await page.locator(".notification-details summary").first().click();
  await expect(page.locator(".notification-details").first()).toContainText(cardIds[0]);
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true,
    value: { writeText: () => Promise.reject(new Error("blocked")) } }));
  await page.getByLabel("Copy severity").selectOption("warning");
  await page.getByRole("button", { name: "Copy JSON" }).click();
  const exported = JSON.parse(await page.getByRole("textbox", { name: "Notification JSON" }).inputValue()) as {
    severityThreshold: string; notifications: Array<{ details?: { cardIds?: string[] } }>;
  };
  expect(exported.severityThreshold).toBe("warning");
  expect(exported.notifications.find((record) => record.details?.cardIds?.length === 9)?.details?.cardIds).toEqual(cardIds);
});

test("an older prepared response cannot replace a newer saved phone queue", async ({ page }) => {
  const newerCards = [preparedCards[0], preparedCards[1], {
    ...preparedCards[1], id: "newer-third", queue_entry_id: 503, repertoire_name: "Newer third card",
  }];
  const newerPreparationTime = new Date(Date.now() + 60_000).toISOString();
  const olderPreparationTime = new Date().toISOString();
  let serveOlderSnapshot = false;
  const currentCards = () => serveOlderSnapshot ? preparedCards : newerCards;
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    local_date: localDate, count: currentCards().length, cards: currentCards(),
  } }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ json: {
    local_date: localDate, count: currentCards().length, cards: currentCards(),
    prepared_at: serveOlderSnapshot ? olderPreparationTime : newerPreparationTime,
    projection: { state: "ready", generation: 1, updated_at: null,
      refresh_pending: 0, last_error: null, blocked_count: 0 },
  } }));
  await page.goto("/");
  await expect(page.getByText(`Phone queue prepared for ${localDate}.`)).toBeVisible();
  serveOlderSnapshot = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByText(/A newer phone queue is already saved/)).toBeVisible();
  const savedCardIds = await page.evaluate(() => new Promise<string[]>((resolve, reject) => {
    const opened = indexedDB.open("tempo-offline-training", 1);
    opened.onerror = () => reject(opened.error);
    opened.onsuccess = () => {
      const request = opened.result.transaction("training").objectStore("training").get("prepared-daily-queue");
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result.cards.map((card: { id: string }) => card.id));
    };
  }));
  expect(savedCardIds).toContain("newer-third");
});

test("complete queue reconciliation keeps a removed in-progress board paused until Retry", async ({ page }) => {
  const activeCard = { ...preparedCards[0], moves: ["e2e4", "e7e5", "g1f3"],
    repertoire_name: "In-progress card" };
  const nextCard = { ...preparedCards[1], repertoire_name: "Next live card" };
  let canonicalCards = [activeCard, nextCard];
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    local_date: localDate, count: canonicalCards.length, cards: canonicalCards,
  } }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ json: {
    local_date: localDate, count: canonicalCards.length, cards: canonicalCards,
    prepared_at: new Date().toISOString(),
    projection: { state: "ready", generation: 1, updated_at: null,
      refresh_pending: 0, last_error: null, blocked_count: 0 },
  } }));
  await page.goto("/");
  await expect(page.getByText("In-progress card")).toBeVisible();
  await playBoardSquare(page, "e2");
  await playBoardSquare(page, "e4");
  canonicalCards = [nextCard];
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByRole("main").getByText(/active card is no longer in today's queue/)).toBeVisible();
  await expect(page.getByText("In-progress card")).toBeVisible();
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText("Next live card")).toBeVisible();
});

test("prepared phone queue validates study metadata beyond the live window and keeps offline exercises", async ({ page }) => {
  const cards: Array<Record<string, unknown>> = Array.from({ length: 20 }, (_, index) => ({
    ...preparedCards[0], id: `opening-${index}`, queue_entry_id: 700 + index,
    study_exercise_id: null,
  }));
  cards.push({
    ...preparedCards[0], id: "study-exercise", queue_entry_id: 720,
    content_type: "study_exercise", kind: "exercise", moves: [],
    repertoire_id: null, study_id: "study-1", study_exercise_id: "exercise-1",
    study_snapshot: { schema_version: 1, grader_version: 1, exercise_id: "exercise-1",
      revision: 1, fen: startFen, specification: { type: "choice", prompt: "Choose", hint: "",
        explanation: "", options: [{ id: "a", text: "A" }], correct_option_ids: ["a"] } },
  });
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: {
    local_date: localDate, count: cards.length, cards: cards.slice(0, 20),
  } }));
  await page.route("**/api/queue/prepared", (route) => route.fulfill({ json: {
    local_date: localDate, count: cards.length, cards,
    prepared_at: new Date().toISOString(),
    projection: { state: "ready", generation: 1, updated_at: null,
      refresh_pending: 0, last_error: null, blocked_count: 0 },
  } }));
  await page.goto("/");
  await expect(page.getByText(`Phone queue prepared for ${localDate}.`)).toBeVisible();
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByText("First phone card")).toBeVisible();
  await expect(page.getByRole("main").getByText(/Offline queue prepared/)).toBeVisible();
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
  await expect(page.getByRole("main").getByText(/The local queue could not be loaded/)).toBeVisible();
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
  await expect(page.locator(".notification-viewport").getByText("Guided attempt saved on phone.")).toBeVisible();
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
