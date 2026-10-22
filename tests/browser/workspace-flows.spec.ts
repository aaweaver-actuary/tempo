import {
  test,
  expect,
  api,
  pgn,
  nav,
} from "./product-fixtures";

test("training Bury hides the card for today across reload and reports a failed bury", async ({ page, request }) => {
  const settings = await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`, { data: { ...settings, new_cards_per_day: 10 } });
  const imported = await request.post(`${api}/imports/pgn`, {
    multipart: {
      file: {
        name: "bury-training.pgn",
        mimeType: "application/x-chess-pgn",
        buffer: Buffer.from(pgn),
      },
      initial_depth: "2",
    },
  });
  expect(imported.ok()).toBeTruthy();
  // Cards can appear before graph/integrity publication and the final queue ordering.
  // This scenario tests burial recovery, so establish its published input before opening an attempt.
  await expect.poll(async () => {
    const repertoires = (await (await request.get(`${api}/repertoires`, {
      headers: { "X-Tempo-Work-Class": "background" },
    })).json()).repertoires;
    const repertoire = repertoires.find((item: { name: string }) => item.name === "bury-training");
    return { graph: repertoire?.graph_state, integrity: repertoire?.integrity_status, scan: repertoire?.integrity_scan_status };
  }, { timeout: 20_000 }).toEqual({ graph: "ready", integrity: "clean", scan: "idle" });
  await expect.poll(async () => {
    const queue = await (await request.get(`${api}/queue/today`, {
      headers: { "X-Tempo-Work-Class": "background" },
    })).json();
    return { ready: queue.projection?.state === "ready" && !queue.projection.refresh_pending, cards: queue.cards.length > 1 };
  }, { timeout: 20_000 }).toEqual({ ready: true, cards: true });
  await page.goto("/");
  await nav(page, "Train");
  await expect(page.getByRole("heading", { name: "bury-training" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Bury", exact: true })).toBeVisible();
  // A worker can own the network request and bypass page-level routes. Keep the
  // real worker active and intercept the actual outgoing command at its context.
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  let refusedBurialOperationId: string | undefined;
  await page.context().route("**/api/queue/entries/*/bury", async route => {
    expect(route.request().method()).toBe("POST");
    refusedBurialOperationId = route.request().headers()["idempotency-key"];
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Queue defer unavailable" }) });
  });
  await page.getByRole("button", { name: "Bury", exact: true }).click();
  await expect.poll(() => refusedBurialOperationId, { message: "The failed-burial fault must intercept the real command" }).toBeTruthy();
  await expect(page.getByRole("alert")).toContainText("still pending");
  for (const name of ["Bury", "Correct", "Again"]) {
    await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
  }
  await expect(page.getByRole("button", { name: /Restart/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Retry bury", exact: true })).toBeEnabled();
  const board = page.locator(".board-frame").first();
  await expect(board).toHaveAttribute("data-input-enabled", "false");
  const fenBeforeBlockedMove = await board.getAttribute("data-fen");
  const box = (await board.locator(".cg-wrap").boundingBox())!;
  for (const rank of [6.5, 4.5])
    await page.mouse.click(box.x + box.width * 4.5 / 8, box.y + box.height * rank / 8);
  await expect(board).toHaveAttribute("data-fen", fenBeforeBlockedMove!);
  const storedBurial = await page.evaluate(() => {
    const entryId = localStorage.getItem("tempo-pending-burial-entry");
    return { entryId, operationId: localStorage.getItem(`tempo-bury-operation-${entryId}`) };
  });
  expect(storedBurial.entryId).toBeTruthy();
  expect(storedBurial.operationId).toBeTruthy();
  expect(storedBurial.operationId).toBe(refusedBurialOperationId);
  await page.reload();
  await nav(page, "Train");
  await expect(page.getByRole("button", { name: "Retry bury", exact: true })).toBeEnabled();
  for (const name of ["Bury", "Correct", "Again"])
    await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "false");
  expect(await page.evaluate(() => localStorage.getItem(`tempo-bury-operation-${localStorage.getItem("tempo-pending-burial-entry")}`))).toBe(storedBurial.operationId);
  await page.context().unroute("**/api/queue/entries/*/bury");
  const retriedBurial = page.waitForRequest(request => request.method() === "POST" && request.url().endsWith("/bury"));
  const before = (await (await request.get(`${api}/queue/today`)).json()).cards;
  await page.getByRole("button", { name: "Retry bury" }).click();
  const retryRequest = await retriedBurial;
  expect(retryRequest.url()).toContain(`/entries/${storedBurial.entryId}/bury`);
  expect(retryRequest.headers()["idempotency-key"]).toBe(storedBurial.operationId);
  await expect.poll(async () => {
    const after = (await (await request.get(`${api}/queue/today`)).json()).cards;
    return after[0]?.queue_entry_id;
  }).not.toBe(before[0]?.queue_entry_id);
  const after = (await (await request.get(`${api}/queue/today`)).json()).cards;
  expect(after).toHaveLength(before.length - 1);
  expect(after.some((card: { id: string }) => card.id === before[0].id)).toBe(false);
  await expect.poll(() => page.evaluate(() => localStorage.getItem("tempo-pending-burial-entry"))).toBeNull();
  await page.reload();
  await nav(page, "Train");
  await expect(page.getByRole("button", { name: "Bury", exact: true })).toBeVisible();
  const reloaded = await (await request.get(`${api}/queue/today`)).json();
  expect(reloaded.cards.some((card: { id: string }) => card.id === before[0].id)).toBe(false);
  expect(reloaded.count).toBe(before.length - 1);
  const terminalRecoveryRequests: string[] = [];
  page.on("request", request => {
    if (request.method() === "POST" && request.url().endsWith("/bury"))
      terminalRecoveryRequests.push(request.url());
  });
  await page.evaluate(({ entryId, operationId }) => {
    localStorage.setItem("tempo-pending-burial-entry", entryId!);
    localStorage.setItem(`tempo-bury-operation-${entryId}`, operationId!);
  }, storedBurial);
  await page.route(`**/api/operations/${storedBurial.operationId}`, route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ state: "failed", error: { message: "Recovered terminal burial failure" } }),
  }));
  await page.reload();
  await nav(page, "Train");
  await expect(page.getByRole("alert")).toContainText("Recovered terminal burial failure");
  await expect(page.getByRole("button", { name: "Retry bury", exact: true })).toHaveCount(0);
  for (const name of ["Bury", "Correct", "Again"])
    await expect(page.getByRole("button", { name, exact: true })).toBeEnabled();
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-burial-entry"))).toBeNull();
  expect(await page.evaluate(entryId => localStorage.getItem(`tempo-bury-operation-${entryId}`), storedBurial.entryId)).toBeNull();
  expect(await page.evaluate(entryId => localStorage.getItem(`tempo-bury-operation-${entryId}`), reloaded.cards[0].queue_entry_id)).toBeNull();
  expect(terminalRecoveryRequests).toEqual([]);
  const terminalQueue = await (await request.get(`${api}/queue/today`)).json();
  expect(terminalQueue.cards[0].queue_entry_id).toBe(reloaded.cards[0].queue_entry_id);
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
  await page
    .locator(".repertoire-card")
    .first()
    .locator("details.card-menu summary")
    .click();
  await page
    .locator(".repertoire-card")
    .first()
    .getByRole("menuitem", { name: "Delete", exact: true })
    .click();
  await page.getByRole("radio", { name: "Delete learned cards" }).check();
  await page.getByRole("button", { name: "Delete repertoire", exact: true }).click();
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
  await nav(page, "Train");
  await nav(page, "Repertoire");
  await page.reload();
  await nav(page, "Repertoire");
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
});



test("Repertoire PGN import publishes a queue card after pending issuance", async ({
  page,
  request,
}) => {
  const settings = await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`, {
    data: { ...settings, new_cards_per_day: 100 },
  });
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "branches.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(
      '[Event "QGD"]\n\n1. d4 d5 2. c4 e6 3. Nc3 *\n\n[Event "Nimzo"]\n\n1. d4 Nf6 2. c4 e6 3. Nc3 Bb4 4. e3 *',
    ),
  });
  const [importResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes("/api/imports/pgn") && response.request().method() === "POST"),
    page.getByRole("button", { name: "Import repertoire", exact: true }).click(),
  ]);
  const importedRepertoireId = (await importResponse.json()).repertoire_id as string;
  expect(importedRepertoireId).toEqual(expect.any(String));
  let pendingIssuanceProbe = true;
  await expect.poll(async () => {
    // The real service can report pending issuance without a cards array.
    // Include that observed boundary before proving the real imported queue.
    const queueResponse = pendingIssuanceProbe ? {
      ok: () => false, status: () => 503,
      text: async () => "Queue changed or attempt issuance is pending. Retry loading the queue.",
      json: async () => ({ detail: "Queue changed or attempt issuance is pending. Retry loading the queue." }),
    } : await request.get(`${api}/queue/today`);
    pendingIssuanceProbe = false;
    if (!queueResponse.ok()) return `HTTP ${queueResponse.status()}: ${await queueResponse.text()}`;
    const queue = await queueResponse.json();
    return (queue.cards as { repertoire_id: string }[]).some((card) => card.repertoire_id === importedRepertoireId);
  }, { timeout: 30_000 }).toBe(true);
  const viewImportedRepertoire = page.getByRole("button", { name: "View imported repertoire" });
  if (await viewImportedRepertoire.isVisible()) await viewImportedRepertoire.click();
  else await page.getByRole("button", { name: "Close import dialog" }).click();
});

test("Black-side Study prompt follows the imported route without recording a review", async ({ page, request }) => {
  const { Chess } = await import("chess.js");
  const { playMove } = await import("./keyboard-fixtures");
  const originalSan = ["e4", "c5", "Nf3", "d5", "exd5", "Qxd5", "g3", "Nc6", "Bg2", "e5"];
  const position = new Chess();
  const positions = [position.fen()];
  const originalUci = originalSan.map(san => { const move = position.move(san); positions.push(position.fen()); return `${move.from}${move.to}`; });
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: "training-study-context.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from('[Event "Training Study context"]\n\n1. e4 c5 2. Nf3 d5 3. exd5 Qxd5 4. g3 Nc6 5. Bg2 e5 *') },
    trained_color: "black", initial_depth: "5",
  } });
  expect(imported.ok()).toBeTruthy();
  const repertoireId = (await imported.json()).repertoire_id as string;
  await expect.poll(async () => {
    const repertoires = (await (await request.get(`${api}/repertoires`)).json()).repertoires;
    const repertoire = repertoires.find((item: { id: string }) => item.id === repertoireId);
    const queue = await (await request.get(`${api}/queue/today`)).json();
    return { graph: repertoire?.graph_state, integrity: repertoire?.integrity_status, ready: queue.projection?.state === "ready" && !queue.projection.refresh_pending,
      completeRoute: queue.cards.some((card: { repertoire_id: string; moves: string[] }) => card.repertoire_id === repertoireId && card.moves.join(" ") === originalUci.join(" ")) };
  }, { timeout: 30_000 }).toEqual({ graph: "ready", integrity: "clean", ready: true, completeRoute: true });
  await page.goto("/");
  const board = page.locator(".board-frame").first();
  await expect(board).toHaveAttribute("data-fen", positions[1]);
  for (const [from, to, replyPly] of [["c7", "c5", 3], ["d7", "d5", 5], ["d8", "d5", 7], ["b8", "c6", 9]] as const) {
    await playMove(page, board, from, to);
    await expect(board).toHaveAttribute("data-fen", positions[replyPly]);
  }
  const reviewsBefore = await page.evaluate(() => localStorage.getItem("tempo-pending-training-reviews-v1"));
  const reviewWrites: string[] = [];
  page.on("request", request => { if (request.method() === "POST" && /\/reviews?(?:\?|$)/.test(request.url())) reviewWrites.push(request.url()); });
  await page.keyboard.press("ArrowLeft");
  await expect(board).toHaveAttribute("data-fen", positions[8]);
  expect(reviewWrites).toEqual([]);
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-training-reviews-v1"))).toBe(reviewsBefore);
});
