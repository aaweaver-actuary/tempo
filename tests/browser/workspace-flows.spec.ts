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
  // Cards can appear before graph/integrity publication and the final queue ordering.
  // This scenario tests burial recovery, so establish its published input before opening an attempt.
  await expect.poll(async () => {
    const repertoires = (await (await request.get(`${api}/repertoires`)).json()).repertoires;
    const repertoire = repertoires.find((item: { name: string }) => item.name === "bury-training");
    return { graph: repertoire?.graph_state, integrity: repertoire?.integrity_status, scan: repertoire?.integrity_scan_status };
  }, { timeout: 20_000 }).toEqual({ graph: "ready", integrity: "clean", scan: "idle" });
  await expect.poll(async () => {
    const queue = await (await request.get(`${api}/queue/today`)).json();
    return { ready: queue.projection?.state === "ready" && !queue.projection.refresh_pending, cards: queue.cards.length > 1 };
  }, { timeout: 20_000 }).toEqual({ ready: true, cards: true });
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

test("training Builder restores the viewed mid-line position and durably saves Bg4 on the original route", async ({ page, request }) => {
  const { Chess } = await import("chess.js");
  const { expectedPieces, renderedPieces, playMove } = await import("./keyboard-fixtures");
  const originalSan = ["e4", "c5", "Nf3", "d5", "exd5", "Qxd5", "g3", "Nc6", "Bg2", "e5"];
  const position = new Chess();
  const positions = [position.fen()];
  const originalUci = originalSan.map(san => { const move = position.move(san); positions.push(position.fen()); return `${move.from}${move.to}`; });
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: "training-builder-context.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from('[Event "Training Builder context"]\n\n1. e4 c5 2. Nf3 d5 3. exd5 Qxd5 4. g3 Nc6 5. Bg2 e5 *') },
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
  await page.getByLabel("Open review position").getByRole("button", { name: "Builder", exact: true }).click();
  await expect(page.getByRole("button", { name: "Save branch", exact: true })).toBeDisabled();
  await expect.poll(() => page.evaluate(() => {
    const session = JSON.parse(localStorage.getItem("tempo-builder-session")!);
    return { cursor: session.cursor, length: session.history.length, root: session.startingFen, pending: Boolean(session.trainingRouteToResolve) };
  })).toEqual({ cursor: 8, length: 10, root: positions[0], pending: false });
  await expect(board).toHaveAttribute("data-fen", positions[8]);
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(positions[8]));
  await expect(page.locator(".repertoire-panel")).toContainText("Bg2");
  await expect(page.getByText("No saved response at this position.")).toHaveCount(0);
  await page.getByRole("button", { name: /Forward/ }).click();
  await expect(board).toHaveAttribute("data-fen", positions[9]);
  await page.getByRole("button", { name: /Back/ }).click();
  await page.getByRole("button", { name: /Back/ }).click();
  await expect(board).toHaveAttribute("data-fen", positions[7]);
  await playMove(page, board, "c8", "g4");
  const alternativePosition = new Chess(positions[7]); alternativePosition.move("Bg4");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(alternativePosition.fen()));
  const savedRequest = page.waitForRequest(request => request.method() === "POST" && request.url().endsWith("/api/repertoire/branches"));
  await page.getByRole("button", { name: "Save branch", exact: true }).click();
  expect((await savedRequest).postDataJSON()).toMatchObject({ repertoire_id: repertoireId, starting_fen: positions[0], moves: [...originalUci.slice(0, 7), "c8g4"] });
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await expect.poll(async () => {
    const lines = (await (await request.get(`${api}/repertoire/lines`)).json()).lines.filter((line: { repertoire_id: string }) => line.repertoire_id === repertoireId);
    return lines.map((line: { moves: string[]; start_fen: string }) => ({ moves: line.moves, root: line.start_fen }));
  }).toEqual(expect.arrayContaining([{ moves: originalUci, root: positions[0] }, { moves: [...originalUci.slice(0, 7), "c8g4"], root: positions[0] }]));
  await nav(page, "Train");
  await expect(board).toHaveAttribute("data-fen", positions[9]);
  expect(reviewWrites).toEqual([]);
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-training-reviews-v1"))).toBe(reviewsBefore);
  await nav(page, "Builder");
  await page.reload();
  await nav(page, "Builder");
  await expect(board).toHaveAttribute("data-fen", alternativePosition.fen());
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(alternativePosition.fen()));
});

for (const width of [390, 1470]) {
  test(`partial training Builder route chooser keeps the board fixed at ${width}`, async ({ page, request }) => {
    const { Chess } = await import("chess.js");
    const { expectedPieces, renderedPieces } = await import("./keyboard-fixtures");
    await page.setViewportSize({ width, height: 900 });
    const imported = await request.post(`${api}/imports/pgn`, { multipart: {
      file: { name: "transposed-training.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from('[Event "Knight first"]\n\n1. Nf3 Nf6 2. g3 g6 3. Bg2 Bg7 *\n\n[Event "Pawn first"]\n\n1. g3 g6 2. Nf3 Nf6 3. Bg2 Bg7 *') }, trained_color: "black", initial_depth: "2",
    } });
    expect(imported.ok()).toBeTruthy();
    const repertoireId = (await imported.json()).repertoire_id as string;
    const position = new Chess();
    for (const san of ["Nf3", "Nf6", "g3", "g6"]) position.move(san);
    const startingFen = position.fen();
    const history = ["Bg2", "Bg7"].map(san => { const move = position.move(san); return { san: move.san, uci: `${move.from}${move.to}`, fen: position.fen() }; });
    await page.addInitScript(session => {
      localStorage.setItem("tempo-builder-session", JSON.stringify(session));
      localStorage.setItem("tempo-stockfish-on", "false");
      localStorage.setItem("tempo-maia-on", "false");
      sessionStorage.setItem("tempo-builder-tools", "Repertoire");
    }, { version: 1, activeRepertoireId: repertoireId, activeRepertoireByColor: { black: repertoireId }, orientation: "black", startingFen, history, cursor: 0, branchStart: 0,
      trainingRouteToResolve: { repertoireId, cardId: "partial-training-card", cardRevision: 1 } });
    await page.goto("/"); await nav(page, "Builder");
    const board = page.locator(".board-frame").first();
    await expect(page.getByText(/Several earlier move orders/)).toBeVisible();
    await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(startingFen));
    await expect(page.getByRole("button", { name: "Save branch", exact: true })).toBeDisabled();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/training-builder/route-choice-${width}.png`, fullPage: true });
    await page.getByRole("button", { name: /g3 · g6 · Nf3 · Nf6/ }).click();
    await expect.poll(() => page.evaluate(() => {
      const session = JSON.parse(localStorage.getItem("tempo-builder-session")!);
      return { cursor: session.cursor, pending: Boolean(session.trainingRouteToResolve), prefix: session.history.slice(0, 4).map((move: { san: string }) => move.san) };
    })).toEqual({ cursor: 4, pending: false, prefix: ["g3", "g6", "Nf3", "Nf6"] });
    await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(startingFen));
    await expect(page.getByText(/Several earlier move orders/)).toHaveCount(0);
  });
}
