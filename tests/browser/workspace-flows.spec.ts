import {
  test,
  expect,
  api,
  pgn,
  nav,
  boardVisible,
  move,
} from "./product-fixtures";
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
    .poll(async () => (await (await request.get(`${api}/queue/today`)).json()).count)
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
  await page.getByRole("button", { name: "View imported repertoire" }).click();
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
