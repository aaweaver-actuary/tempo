import {
  test,
  expect,
  api,
  pgn,
  nav,
  boardVisible,
  move,
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
  await expect.poll(async () => {
    const queue = await (await request.get(`${api}/queue/today`)).json();
    return queue.cards.length;
  }, { timeout: 20_000 }).toBeGreaterThan(1);
  await page.goto("/");
  await nav(page, "Train");
  await expect(page.getByRole("heading", { name: "bury-training" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Bury", exact: true })).toBeVisible();
  await page.route("**/api/queue/entries/*/bury", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Queue defer unavailable" }) }));
  await page.getByRole("button", { name: "Bury", exact: true }).click();
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
  await page.reload();
  await nav(page, "Train");
  await expect(page.getByRole("button", { name: "Retry bury", exact: true })).toBeEnabled();
  for (const name of ["Bury", "Correct", "Again"])
    await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "false");
  expect(await page.evaluate(() => localStorage.getItem(`tempo-bury-operation-${localStorage.getItem("tempo-pending-burial-entry")}`))).toBe(storedBurial.operationId);
  await page.unroute("**/api/queue/entries/*/bury");
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
  page.on("dialog", (dialog) => dialog.accept());
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
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
  await nav(page, "Builder");
  await nav(page, "Repertoire");
  await page.reload();
  await nav(page, "Repertoire");
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
});

test("Builder exact transposition confirms a conflicting trained move then saves the route", async ({
  page,
  request,
}) => {
  const settings = await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`, {
    data: { ...settings, new_cards_per_day: 10 },
  });
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
  await page.getByRole("tab", { name: "Repertoire", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
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

test("review position opens consistently in analysis builder and games", async ({
  page,
  request,
}) => {
  const settings = await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`, {
    data: { ...settings, new_cards_per_day: 10 },
  });
  await request.post(`${api}/imports/pgn`, {
    multipart: {
      file: {
        name: "handoff.pgn",
        mimeType: "application/x-chess-pgn",
        buffer: Buffer.from(pgn),
      },
      initial_depth: "2",
    },
  });
  await expect
    .poll(async () => (await (await request.get(`${api}/queue/today`)).json()).count,
      { timeout: 30_000 })
    .toBeGreaterThan(0);
  await page.goto("/");
  const reviewedFen = await page.locator(".board-frame").getAttribute("data-fen");
  await nav(page, "Builder");
  await page.getByRole("tab", { name: "Compare", exact: true }).click();
  await expect(page.locator(".analysis-page")).toHaveAttribute(
    "data-active-task",
    "Compare",
  );
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", reviewedFen!);

  await nav(page, "Train");
  await page
    .getByLabel("Open review position")
    .getByRole("button", { name: "Builder", exact: true })
    .click();
  await expect(page.locator(".analysis-page")).toHaveAttribute(
    "data-active-task",
    "Repertoire",
  );
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", reviewedFen!);

  await nav(page, "Train");
  await page.getByRole("button", { name: "Games here", exact: true }).click();
  await expect(page.getByText(/Position filter · 0 encounters/)).toBeVisible();
});

test("Edit card opens Builder line-removal context and deletes the selected branch", async ({
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
  await expect.poll(async () => {
    const queue = await (await request.get(`${api}/queue/today`)).json();
    return (queue.cards as { repertoire_id: string }[]).some((card) => card.repertoire_id === importedRepertoireId);
  }, { timeout: 30_000 }).toBe(true);
  const viewImportedRepertoire = page.getByRole("button", { name: "View imported repertoire" });
  if (await viewImportedRepertoire.isVisible()) await viewImportedRepertoire.click();
  else await page.getByRole("button", { name: "Close import dialog" }).click();
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
  await page.getByRole("tab", { name: "Repertoire", exact: true }).click();
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
